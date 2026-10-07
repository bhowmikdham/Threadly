"""Public draft consent uses existing account-bound OAuth and never creates email."""

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select

from app.auth import flow, service
from app.config import get_settings
from app.db.models import AssistantAction, GmailDraftSave
from tests.conftest import needs_pg
from tests.test_auth_flow import google_transport
from tests.test_oauth_boundaries import READ, REDIRECT, VERIFIER, begin, configured, exchange_body

__all__ = ["configured"]
pytestmark = needs_pg
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"


def login(client):
    response = client.post("/auth/google/exchange", json=exchange_body(begin(client)))
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["jwt"]}


def consent(client, headers, capabilities=None):
    return client.post(
        "/auth/google/reconnect",
        headers=headers,
        json={
            "redirect_uri": REDIRECT,
            "code_challenge": flow.challenge(VERIFIER),
            "capabilities": capabilities or ["gmail_draft"],
        },
    )


async def test_normal_account_requests_compose_without_send_eligibility_or_email_creation(
    secure_db_client,
    configured,
    monkeypatch,
    db_sessionmaker,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "email_writes_enabled", True)
    monkeypatch.setattr(settings, "write_pilot_user_ids", "999999")
    headers = login(secure_db_client)
    assert consent(secure_db_client, None).status_code == 401
    start = consent(secure_db_client, headers)
    assert start.status_code == 200, start.text
    start = start.json()
    params = parse_qs(urlsplit(start["authorization_url"]).query)
    assert set(params["scope"][0].split()) == {"openid", "email", "profile", READ, COMPOSE}
    assert params["code_challenge"] == [flow.challenge(VERIFIER)]
    caps = secure_db_client.get("/assistant/capabilities", headers=headers).json()
    assert "gmail_draft" in caps["reconnect"]["requestable_capabilities"]
    assert "gmail_send" not in caps["reconnect"]["requestable_capabilities"]
    assert not next(c for c in caps["capabilities"] if c["id"] == "gmail_draft")["ready"]
    assert consent(secure_db_client, headers, ["gmail_draft", "gmail_send"]).status_code == 409

    async def granted(*args, **kwargs):
        return await configured(
            *args, **kwargs, transport=google_transport(scopes=READ + " " + COMPOSE)
        )

    monkeypatch.setattr(service, "exchange_code", granted)
    assert (
        secure_db_client.post(
            "/auth/google/exchange", json=exchange_body(start, code_verifier="b" * 64)
        ).status_code
        == 400
    )
    result = secure_db_client.post("/auth/google/exchange", json=exchange_body(start))
    assert result.status_code == 200, result.text
    headers = {"Authorization": "Bearer " + result.json()["jwt"]}
    caps = secure_db_client.get("/assistant/capabilities", headers=headers).json()
    by_id = {c["id"]: c for c in caps["capabilities"]}
    assert by_id["gmail_draft"]["ready"]
    assert not by_id["gmail_send"]["enabled"] and not by_id["gmail_send"]["ready"]
    assert (
        secure_db_client.post("/auth/google/exchange", json=exchange_body(start)).status_code == 400
    )
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(GmailDraftSave)) == 0
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0


@pytest.mark.parametrize("outcome", ["reduced_grant", "wrong_account", "signed_out"])
def test_draft_consent_requires_actual_grant_same_account_and_live_session(
    secure_db_client,
    configured,
    monkeypatch,
    outcome,
):
    headers = login(secure_db_client)
    start = consent(secure_db_client, headers).json()
    if outcome == "signed_out":
        assert secure_db_client.post("/auth/logout", headers=headers).status_code == 200

    async def exchange(*args, **kwargs):
        return await configured(
            *args,
            **kwargs,
            transport=google_transport(
                scopes=READ if outcome == "reduced_grant" else READ + " " + COMPOSE,
                sub="another-account" if outcome == "wrong_account" else "g-sub-1",
            ),
        )

    monkeypatch.setattr(service, "exchange_code", exchange)
    result = secure_db_client.post("/auth/google/exchange", json=exchange_body(start))
    if outcome == "reduced_grant":
        assert result.status_code == 200, result.text
        fresh = {"Authorization": "Bearer " + result.json()["jwt"]}
        caps = secure_db_client.get("/assistant/capabilities", headers=fresh).json()
        assert not next(c for c in caps["capabilities"] if c["id"] == "gmail_draft")["ready"]
    elif outcome == "signed_out":
        assert result.status_code == 400, result.text
        assert result.json()["error"]["code"] == "oauth_state_invalid"
    else:
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "google_account_mismatch"
