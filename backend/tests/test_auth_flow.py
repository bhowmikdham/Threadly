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
    email: str = "b@x.com",
    name: str = "Bhowmik",
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
                    "email": email,
                    "name": name,
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


@pytest.mark.asyncio
async def test_two_google_subjects_get_separate_accounts_and_calendar_settings(
    db_sessionmaker, db_client, monkeypatch
):
    from sqlalchemy import select

    from app.auth import crypto, service
    from app.calendar import service as calendar_service
    from app.db.models import CalendarPreference, User
    from app.schemas.calendar import POLICY_VERSION

    monkeypatch.setattr(calendar_service, "get_session_factory", lambda: db_sessionmaker)

    grants = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
        "https://www.googleapis.com/auth/calendar.events.freebusy"
    )
    identities = [
        ("google-person-a", "person-a@example.test", "access-a", "calendar-a"),
        ("google-person-b", "person-b@example.test", "access-b", "calendar-b"),
    ]
    sessions = []
    for subject, email, access, calendar_id in identities:
        async with db_sessionmaker.begin() as session:
            token, user = await service.exchange_code(
                session,
                "synthetic-code",
                "https://ext.chromiumapp.org/",
                transport=google_transport(
                    sub=subject,
                    email=email,
                    access_token=access,
                    refresh_token="refresh-" + subject,
                    scopes=grants,
                ),
            )
            sessions.append((token, user.id, email, access, calendar_id))
            session.add(
                CalendarPreference(
                    user_id=user.id,
                    version=1,
                    account_version=user.google_account_version,
                    policy_version=POLICY_VERSION,
                    preferences={
                        "timezone": "Australia/Melbourne",
                        "calendar_ids": [calendar_id],
                        "working_periods": [
                            {"weekday": 0, "start_minute": 540, "end_minute": 1020}
                        ],
                        "buffer_before_minutes": 0,
                        "buffer_after_minutes": 0,
                        "minimum_notice_minutes": 60,
                        "default_duration_minutes": 30,
                    },
                )
            )

    assert sessions[0][1] != sessions[1][1]
    async with db_sessionmaker() as session:
        users = (await session.execute(select(User).order_by(User.id))).scalars().all()
        assert [user.google_sub for user in users] == [person[0] for person in identities]
        assert [crypto.decrypt_token(user.access_token_enc) for user in users] == [
            "access-a",
            "access-b",
        ]

    users_by_id = {user.id: user for user in users}
    for token, owner, email, _access, calendar_id in sessions:
        headers = {"Authorization": "Bearer " + token}
        capabilities = db_client.get("/assistant/capabilities", headers=headers)
        assert capabilities.status_code == 200
        assert capabilities.json()["account"]["connected"] is True
        by_id = {item["id"]: item for item in capabilities.json()["capabilities"]}
        assert by_id["calendar_list"]["ready"] and by_id["calendar_read"]["ready"]
        preferences = db_client.get("/calendar/preferences", headers=headers)
        assert preferences.status_code == 200
        assert preferences.json()["preferences"]["calendar_ids"] == [calendar_id]
        assert capabilities.json()["account"]["account_version"] == 1
        assert users_by_id[owner].email == email


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
