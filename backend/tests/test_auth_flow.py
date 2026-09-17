"""OAuth persistence, capability readiness and refresh lifecycle against PostgreSQL."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.conftest import needs_pg

pytestmark = needs_pg


def google_transport(
    refresh_token: str | None = "rt-1",
    *,
    access_token: str = "at-1",
    scopes: str | None = None,
    sub: str = "g-sub-1",
    email_verified: bool = True,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            body = {"access_token": access_token, "expires_in": 3600}
            if refresh_token:
                body["refresh_token"] = refresh_token
            if scopes is not None:
                body["scope"] = scopes
            return httpx.Response(200, json=body)
        if request.url.host == "openidconnect.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "sub": sub,
                    "email": "b@x.com",
                    "name": "Bhowmik",
                    "email_verified": email_verified,
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_exchange_creates_user_with_encrypted_tokens(db_sessionmaker):
    from sqlalchemy import select

    from app.auth import crypto, service
    from app.db.models import User

    async with db_sessionmaker() as session:
        token, user = await service.exchange_code(
            session, "code-abc", "https://ext.chromiumapp.org/", transport=google_transport()
        )
        await session.commit()
        assert token and user.id

    async with db_sessionmaker() as session:
        row = (await session.execute(select(User))).scalar_one()
        assert row.email == "b@x.com"
        assert row.access_token_enc != b"at-1"  # encrypted at rest
        assert crypto.decrypt_token(row.access_token_enc) == "at-1"
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-1"


@pytest.mark.asyncio
async def test_second_login_without_refresh_token_keeps_old_one(db_sessionmaker):
    from sqlalchemy import select

    from app.auth import crypto, service
    from app.db.models import User

    async with db_sessionmaker() as session:
        await service.exchange_code(
            session, "c1", "https://r/", transport=google_transport("rt-first")
        )
        await session.commit()
    async with db_sessionmaker() as session:
        await service.exchange_code(session, "c2", "https://r/", transport=google_transport(None))
        await session.commit()
    async with db_sessionmaker() as session:
        row = (await session.execute(select(User))).scalar_one()
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-first"  # never nulled


def test_jwt_from_exchange_opens_protected_routes(db_client, auth_headers):
    r = db_client.get("/threads", headers=auth_headers(1))
    assert r.status_code == 200  # empty list, but authorised
    assert r.json() == {"threads": [], "next_page": None}


@pytest.mark.asyncio
async def test_capabilities_use_actual_grants_without_enabling_writes(
    db_sessionmaker, db_client, auth_headers
):
    from app.auth import service

    async with db_sessionmaker() as session:
        _, user = await service.exchange_code(
            session,
            "code-abc",
            "https://ext.chromiumapp.org/",
            transport=google_transport(scopes="https://www.googleapis.com/auth/gmail.readonly"),
        )
        await session.commit()

    response = db_client.get("/assistant/capabilities", headers=auth_headers(user.id))
    assert response.status_code == 200
    capabilities = {item["id"]: item for item in response.json()["capabilities"]}
    assert capabilities["gmail_read"]["status"] == "ready"
    assert capabilities["gmail_send"]["ready"] is False
    assert capabilities["gmail_send"]["status"] == "disabled"
    assert capabilities["calendar_write"]["ready"] is False
    assert capabilities["calendar_write"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_refresh_preserves_token_and_caller_rollback_does_not_commit_changes(
    db_sessionmaker, monkeypatch
):
    from app.auth import crypto, service
    from app.db.models import User

    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    async with db_sessionmaker() as session:
        _, user = await service.exchange_code(
            session, "code", "https://r/", transport=google_transport("rt-first")
        )
        await session.commit()
        user_id = user.id
        user.access_token_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with db_sessionmaker() as caller_session:
        user = await caller_session.get(User, user_id)
        user.access_token_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        user.display_name = "must-not-commit"
        token = await service.get_valid_access_token(
            caller_session,
            user,
            transport=google_transport(None, access_token="at-refreshed"),
        )
        assert token == "at-refreshed"
        await caller_session.rollback()

    async with db_sessionmaker() as session:
        row = await session.get(User, user_id)
        assert crypto.decrypt_token(row.access_token_enc) == "at-refreshed"
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-first"
        assert row.display_name == "Bhowmik"


@pytest.mark.asyncio
async def test_stale_refresh_cannot_restore_disconnected_account(db_sessionmaker, monkeypatch):
    from app.auth import google, service
    from app.db.models import User

    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    async with db_sessionmaker() as session:
        _, user = await service.exchange_code(
            session, "code", "https://r/", transport=google_transport("rt-first")
        )
        await session.commit()
        user_id = user.id
        account_version = user.google_account_version

    assert await service.disconnect_google_account(user_id, expected_version=account_version)
    persisted = await service._persist_refreshed_tokens(
        user_id,
        observed_version=account_version,
        tokens=google.GoogleTokens("late-access", 3600, "late-refresh", []),
        now=datetime.now(UTC),
    )
    assert persisted is False

    async with db_sessionmaker() as session:
        row = await session.get(User, user_id)
        assert row.google_connected is False
        assert row.access_token_enc is None
        assert row.refresh_token_enc is None
