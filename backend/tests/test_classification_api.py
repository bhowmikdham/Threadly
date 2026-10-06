"""Real disposable PostgreSQL, authenticated HTTP, fake Gmail and model responses."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from sqlalchemy import func, select, text

from app.classification import service
from app.config import get_settings
from app.db.models import ContextSnapshot, Message, Thread, User
from tests.conftest import needs_pg
from tests.test_classification import FakeProvider, decision
from tests.test_on_demand_gmail import TID, setup  # noqa: F401

pytestmark = needs_pg
URL = f"/threads/{TID}/classification"


def headers(owner=1):
    settings = get_settings()
    token = jwt.encode(
        {"sub": str(owner), "av": 1, "sv": 1,
         "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())},
        settings.secret_key, algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def badges(setup, monkeypatch, db_sessionmaker):  # noqa: F811 - shared pytest fixture
    settings = get_settings()
    monkeypatch.setattr(settings, "classification_enabled", True)
    monkeypatch.setattr(settings, "classification_model_id", "test-classifier")
    monkeypatch.setattr(settings, "bedrock_mail_processing_acknowledged", True)

    class NoTransactionModel(FakeProvider):
        async def generate(self, *args, **kwargs):
            async with db_sessionmaker() as session:
                assert await session.scalar(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND state='idle in transaction'"
                )) == 0
            return await super().generate(*args, **kwargs)

    provider = NoTransactionModel()
    monkeypatch.setattr(service, "get_provider", lambda: provider)
    return setup, provider


async def test_authenticated_badges_store_no_source_or_classification(
    badges, secure_db_client, db_sessionmaker,
):
    response = secure_db_client.post(URL, json={"time_zone": "Australia/Melbourne"},
                                     headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["labels"] == decision()["labels"]
    assert body["status"] == "classified" and body["persisted"] is False
    assert body["time_zone"] == "Australia/Melbourne"
    assert body["evidence"]["category"] == ["def456"]
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE-SOURCE-MARKER" not in response.text
    async with db_sessionmaker() as session:
        for model in (Thread, Message, ContextSnapshot):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


def test_endpoint_passes_pinned_flow_to_provider(badges, secure_db_client, monkeypatch):
    from tests.test_classification_flows import target

    entry = target()
    settings = get_settings()
    monkeypatch.setattr(settings, "classification_transport", "bedrock_flow")
    monkeypatch.setattr(settings, "classification_flow_manifest", entry.model_dump_json())
    monkeypatch.setattr(settings, "classification_model_id", "")
    _, provider = badges
    observed = []

    async def flow_generate(*args, **kwargs):
        observed.append(kwargs)
        return json.dumps(decision())

    monkeypatch.setattr(provider, "generate", flow_generate)
    response = secure_db_client.post(URL, json={}, headers=headers())
    assert response.status_code == 200, response.text
    assert observed[0]["flow"] == entry
    assert observed[0]["model"] == entry.model_profile_arn


def test_authentication_and_owner_isolation(badges, secure_db_client):
    _, provider = badges
    assert secure_db_client.post(URL, json={}).status_code == 401
    response = secure_db_client.post(URL, json={}, headers=headers(2))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "gmail_source_missing"
    assert not provider.calls


@pytest.mark.parametrize("body", [
    {"user_id": 2}, {"body": "injected text"}, {"time_zone": "invalid-zone"},
    {"time_zone": 123},
])
def test_request_cannot_supply_identity_or_mail(badges, secure_db_client, body):
    _, provider = badges
    response = secure_db_client.post(URL, json=body, headers=headers())
    assert response.status_code == 422 and not provider.calls
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("change", ["logout", "disconnect", "reconnect"])
def test_session_change_during_model_discards_result(
    badges, secure_db_client, db_sessionmaker, monkeypatch, change,
):
    _, provider = badges

    async def revoked(*args, **kwargs):
        async with db_sessionmaker.begin() as session:
            user = await session.get(User, 1)
            if change == "logout":
                user.threadly_session_version += 1
            elif change == "reconnect":
                user.google_account_version += 1
            else:
                user.google_connected = False
        return json.dumps(decision())

    monkeypatch.setattr(provider, "generate", revoked)
    response = secure_db_client.post(URL, json={}, headers=headers())
    assert response.status_code in (401, 403, 409), response.text
    assert "labels" not in response.json()


def test_model_failure_has_error_envelope_and_no_badges(badges, secure_db_client):
    _, provider = badges
    provider.output = {"labels": {"needs_reply": False, "category": "other", "priority": "Low"}}
    response = secure_db_client.post(URL, json={}, headers=headers())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "classification_output_invalid"
    assert "labels" not in response.json()


async def test_concurrent_old_result_cannot_win_after_new_mail(
    badges, secure_db_client, monkeypatch,
):
    state, provider = badges
    first_started, let_first_finish = asyncio.Event(), asyncio.Event()
    count = 0

    async def overlapping(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            first_started.set()
            await let_first_finish.wait()
        return json.dumps(decision())

    monkeypatch.setattr(provider, "generate", overlapping)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=secure_db_client.app),
                                 base_url="http://test") as client:
        old = asyncio.create_task(client.post(URL, json={}, headers=headers()))
        try:
            await asyncio.wait_for(first_started.wait(), 5)
            state.text = "New current contract. Please review and email your feedback."
            new = await client.post(URL, json={}, headers=headers())
            assert new.status_code == 200, new.text
        finally:
            let_first_finish.set()
        obsolete = await old
    assert obsolete.status_code == 409
    assert obsolete.json()["error"]["code"] == "classification_source_changed"


def test_disabled_endpoint_is_explicit(badges, secure_db_client, monkeypatch):
    _, provider = badges
    monkeypatch.setattr(get_settings(), "classification_enabled", False)
    response = secure_db_client.post(URL, json={}, headers=headers())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "classification_disabled"
    assert not provider.calls
