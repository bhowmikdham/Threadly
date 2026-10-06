"""Calendar rollout never grants Gmail sending, Google scopes, or event approval."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select, text

from app.actions import calendar_executor, gmail_sender
from app.auth import service as auth_service
from app.calendar import permissions
from app.capabilities.service import build_capabilities
from app.config import Settings, get_settings
from app.db.models import ActionJob, GoogleOAuthSession
from tests.conftest import needs_pg
from tests.test_auth_flow import google_transport
from tests.test_calendar_service import setup  # noqa: F401

CALENDAR = "https://www.googleapis.com/auth/calendar.events"
SEND = "https://www.googleapis.com/auth/gmail.send"


def user(uid, scopes):
    return SimpleNamespace(
        id=uid,
        google_scopes=scopes,
        google_connected=True,
        google_email_verified=True,
        google_identity={},
        google_account_version=1,
        access_token_expires_at=None,
        refresh_token_enc=b"synthetic",
        access_token_enc=None,
    )


def test_public_rollout_defaults_off_and_preserves_legacy_eligibility():
    settings = Settings(_env_file=None, write_pilot_user_ids="1")
    assert settings.calendar_public_rollout_enabled is False
    assert settings.calendar_write_eligible(1)
    assert not settings.calendar_write_eligible(2)
    settings.calendar_public_rollout_enabled = True
    assert settings.calendar_write_eligible(2)
    for invalid in (None, 0, -1, True, "1"):
        assert not settings.calendar_write_eligible(invalid)


@pytest.mark.parametrize("uid", [2, 99999])
@pytest.mark.parametrize(
    "scopes,expected", [([], "scope_missing"), (None, "scope_unknown"), ([CALENDAR, SEND], "ready")]
)
def test_current_and_future_accounts_keep_scope_and_gmail_boundaries(
    monkeypatch, uid, scopes, expected
):
    settings = get_settings()
    monkeypatch.setattr(settings, "calendar_writes_enabled", True)
    monkeypatch.setattr(settings, "email_writes_enabled", True)
    monkeypatch.setattr(settings, "write_pilot_user_ids", "1")
    monkeypatch.setattr(settings, "calendar_public_rollout_enabled", True)
    snapshot = build_capabilities(user(uid, scopes))
    caps = {c["id"]: c for c in snapshot["capabilities"]}
    assert caps["calendar_write"]["enabled"]
    assert caps["calendar_write"]["status"] == expected
    assert caps["calendar_write"]["ready"] is (expected == "ready")
    assert not caps["gmail_send"]["enabled"]
    assert not gmail_sender.enabled(user_id=uid)
    assert "calendar_write" in snapshot["reconnect"]["requestable_capabilities"]
    assert "gmail_send" not in snapshot["reconnect"]["requestable_capabilities"]
    monkeypatch.setattr(settings, "calendar_public_rollout_enabled", False)
    assert not calendar_executor.enabled(uid)
    closed = build_capabilities(user(uid, scopes))
    assert not next(c for c in closed["capabilities"] if c["id"] == "calendar_write")["enabled"]
    assert "calendar_write" not in closed["reconnect"]["requestable_capabilities"]


def test_public_rollout_does_not_override_write_kill_switch_or_connection(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "calendar_public_rollout_enabled", True)
    monkeypatch.setattr(settings, "calendar_writes_enabled", False)
    assert not calendar_executor.enabled(2)
    snapshot = build_capabilities(user(2, [CALENDAR]))
    assert not next(c for c in snapshot["capabilities"] if c["id"] == "calendar_write")["ready"]
    monkeypatch.setattr(settings, "calendar_writes_enabled", True)
    disconnected = user(2, [CALENDAR])
    disconnected.google_connected = False
    snapshot = build_capabilities(disconnected)
    cap = next(c for c in snapshot["capabilities"] if c["id"] == "calendar_write")
    assert cap["status"] == "reconnect_required" and not cap["ready"]


@needs_pg
@pytest.mark.parametrize("new_account", [False, True])
async def test_public_consent_is_authenticated_calendar_only_and_does_not_queue_events(
    secure_db_client,
    setup,  # noqa: F811
    db_sessionmaker,
    monkeypatch,
    new_account,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "calendar_public_rollout_enabled", True)
    monkeypatch.setattr(settings, "calendar_writes_enabled", True)
    monkeypatch.setattr(settings, "email_writes_enabled", True)
    monkeypatch.setattr(settings, "write_pilot_user_ids", "1")
    monkeypatch.setattr(settings, "google_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "google_redirect_uri_allowlist", "https://ext.chromiumapp.org/")
    body = {
        "redirect_uri": "https://ext.chromiumapp.org/",
        "code_challenge": "a" * 43,
        "capabilities": ["calendar_write"],
    }
    if new_account:
        async with db_sessionmaker.begin() as session:
            # The shared fixture inserts IDs explicitly; restore normal sequence
            # state before exercising a real new-account OAuth exchange.
            await session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('users', 'id'), "
                    "(SELECT max(id) FROM users))"
                )
            )
            jwt, new_user = await auth_service.exchange_code(
                session,
                "synthetic-code",
                "https://ext.chromiumapp.org/",
                transport=google_transport(
                    sub="future-user", email="future@example.test", scopes="openid email profile"
                ),
            )
            owner = new_user.id
    else:
        owner = 2
        jwt = auth_service.issue_session_jwt(2, 1, 1)
    headers = {"Authorization": "Bearer " + jwt}
    assert secure_db_client.post("/auth/google/begin", json=body).status_code == 422
    assert secure_db_client.post("/auth/google/reconnect", json=body).status_code == 401
    result = secure_db_client.post("/auth/google/reconnect", json=body, headers=headers)
    assert result.status_code == 200, result.text
    scopes = parse_qs(urlsplit(result.json()["authorization_url"]).query)["scope"][0].split()
    assert CALENDAR in scopes and SEND not in scopes
    for requested in (["gmail_send"], ["calendar_write", "gmail_send"]):
        result = secure_db_client.post(
            "/auth/google/reconnect", json={**body, "capabilities": requested}, headers=headers
        )
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "write_pilot_unavailable"
    caps = secure_db_client.get("/assistant/capabilities", headers=headers).json()
    write = next(c for c in caps["capabilities"] if c["id"] == "calendar_write")
    assert write["enabled"] and not write["ready"] and write["scope_status"] == "missing"
    approval = await permissions.get(
        owner, "00000000-0000-4000-8000-000000000001", factory=db_sessionmaker
    )
    assert approval == {"mode": "ask", "version": 0, "scope": "calendar_events_this_chat"}
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(GoogleOAuthSession)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0
