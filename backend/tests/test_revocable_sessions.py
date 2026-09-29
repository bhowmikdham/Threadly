"""A Threadly bearer is useful only for the Google account generation that issued it."""

import asyncio
from types import SimpleNamespace

import jwt
import pytest

from app.api.errors import ApiError
from app.auth import flow, service
from app.config import get_settings
from app.db.models import User
from tests.conftest import needs_pg
from tests.test_auth_flow import google_transport

pytestmark = needs_pg


async def login(db_sessionmaker, *, sub: str, email: str):
    async with db_sessionmaker.begin() as session:
        return await service.exchange_code(
            session,
            "synthetic-code",
            "https://ext.chromiumapp.org/",
            transport=google_transport(sub=sub, email=email, refresh_token="refresh-" + sub),
        )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


@pytest.mark.asyncio
async def test_disconnect_revokes_all_old_threadly_bearers_and_refresh(
    db_sessionmaker, secure_db_client, monkeypatch
):
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    first, user = await login(db_sessionmaker, sub="google-first", email="one@example.test")
    second, other = await login(db_sessionmaker, sub="google-second", email="two@example.test")
    assert user.id != other.id

    renewed = secure_db_client.post("/auth/refresh", headers=bearer(first))
    assert renewed.status_code == 200, renewed.text
    renewed_token = renewed.json()["jwt"]
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(renewed_token)).status_code
        == 200
    )

    gone = secure_db_client.post("/auth/google/disconnect", headers=bearer(first))
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"connected": False, "provider_revocation": "not_requested"}
    for token in (first, renewed_token):
        for path in ("/auth/refresh", "/assistant/capabilities"):
            if path.endswith("refresh"):
                response = secure_db_client.post(path, headers=bearer(token))
            else:
                response = secure_db_client.get(path, headers=bearer(token))
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "reauth_required"
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(second)).status_code == 200
    )

    relogin, reconnected = await login(
        db_sessionmaker, sub="google-first", email="one@example.test"
    )
    assert reconnected.id == user.id
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(relogin)).status_code == 200
    )
    assert secure_db_client.post("/auth/refresh", headers=bearer(first)).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_all_threadly_bearers_without_disconnect(
    db_sessionmaker, secure_db_client, monkeypatch
):
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    first, user = await login(db_sessionmaker, sub="google-logout", email="logout@example.test")
    renewed = secure_db_client.post("/auth/refresh", headers=bearer(first)).json()["jwt"]
    result = secure_db_client.post("/auth/logout", headers=bearer(first))
    assert result.status_code == 200, result.text
    assert result.json() == {"signed_out": True, "scope": "all_sessions"}
    for token in (first, renewed):
        denied = secure_db_client.post("/auth/refresh", headers=bearer(token))
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "reauth_required"

    # A request authenticated just before logout cannot disconnect Google
    # after the session-generation bump commits.
    assert not await service.disconnect_google_account(
        user.id,
        expected_version=user.google_account_version,
        expected_session_version=user.threadly_session_version,
    )

    async with db_sessionmaker() as session:
        persisted = await session.get(User, user.id)
        assert persisted.google_connected is True
        assert persisted.refresh_token_enc is not None
        assert persisted.google_account_version == user.google_account_version
        assert persisted.threadly_session_version == user.threadly_session_version + 1

    relogin, _ = await login(db_sessionmaker, sub="google-logout", email="logout@example.test")
    assert secure_db_client.post("/auth/refresh", headers=bearer(relogin)).status_code == 200


@pytest.mark.asyncio
async def test_logout_fences_pending_and_consumed_reconnect_states(
    db_sessionmaker, secure_db_client, monkeypatch
):
    token, user = await login(db_sessionmaker, sub="google-state", email="state@example.test")
    monkeypatch.setattr(flow, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(flow, "validate_redirect", lambda _uri: None)
    monkeypatch.setattr(
        flow,
        "get_settings",
        lambda: SimpleNamespace(
            google_client_id="synthetic-client",
            google_client_secret="synthetic-secret",
            write_pilot_user_ids_values=set(),
        ),
    )
    redirect = "https://ext.chromiumapp.org/"
    verifier = "v" * 64
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as stale_account:
            await flow.begin(
                session,
                redirect,
                flow.challenge(verifier),
                user_id=user.id,
                expected_account_version=user.google_account_version - 1,
                expected_session_version=user.threadly_session_version,
            )
        assert stale_account.value.code == "reauth_required"
    async with db_sessionmaker.begin() as session:
        pending = await flow.begin(
            session,
            redirect,
            flow.challenge(verifier),
            user_id=user.id,
            expected_account_version=user.google_account_version,
            expected_session_version=user.threadly_session_version,
        )
        consumed = await flow.begin(
            session,
            redirect,
            flow.challenge(verifier),
            user_id=user.id,
            expected_account_version=user.google_account_version,
            expected_session_version=user.threadly_session_version,
        )
    owner, account_version, session_version = await flow.consume(
        consumed["state"], redirect, verifier
    )
    assert owner == user.id
    assert secure_db_client.post("/auth/logout", headers=bearer(token)).status_code == 200

    with pytest.raises(ApiError) as stale_state:
        await flow.consume(pending["state"], redirect, verifier)
    assert stale_state.value.code == "oauth_state_invalid"
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as stale_exchange:
            await service.exchange_code(
                session,
                "synthetic-code",
                redirect,
                expected_user_id=owner,
                expected_version=account_version,
                expected_session_version=session_version,
                transport=google_transport(sub="google-state", email="state@example.test"),
            )
        assert stale_exchange.value.code == "google_connection_changed"
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as stale_begin:
            await flow.begin(
                session,
                redirect,
                flow.challenge(verifier),
                user_id=user.id,
                expected_account_version=account_version,
                expected_session_version=session_version,
            )
        assert stale_begin.value.code == "reauth_required"


@pytest.mark.asyncio
async def test_legacy_missing_deleted_and_wrong_generation_bearers_fail_closed(
    db_sessionmaker, secure_db_client
):
    token, user = await login(db_sessionmaker, sub="google-owner", email="owner@example.test")
    settings = get_settings()
    legacy = jwt.encode(
        {"sub": str(user.id)}, settings.secret_key, algorithm=settings.jwt_algorithm
    )
    missing = service.issue_session_jwt(
        user.id + 100, user.google_account_version, user.threadly_session_version
    )
    wrong_generation = service.issue_session_jwt(
        user.id, user.google_account_version + 1, user.threadly_session_version
    )
    for invalid in (legacy, missing, wrong_generation):
        response = secure_db_client.post("/auth/refresh", headers=bearer(invalid))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "reauth_required"
    assert secure_db_client.post("/auth/refresh", headers=bearer(token)).status_code == 200

    async with db_sessionmaker.begin() as session:
        row = await session.get(User, user.id)
        await session.delete(row)
    assert secure_db_client.post("/auth/refresh", headers=bearer(token)).status_code == 401


@pytest.mark.asyncio
async def test_refresh_under_row_lock_cannot_mint_after_disconnect(db_sessionmaker):
    token, user = await login(db_sessionmaker, sub="google-race", email="race@example.test")
    account_version = user.google_account_version
    async with db_sessionmaker() as disconnect_session:
        row = await disconnect_session.get(User, user.id, with_for_update=True)
        entered = asyncio.Event()

        async def pending_refresh():
            async with db_sessionmaker() as refresh_session:
                entered.set()
                return await service.refresh_session_jwt(
                    refresh_session,
                    user_id=user.id,
                    expected_account_version=account_version,
                    expected_session_version=user.threadly_session_version,
                )

        pending = asyncio.create_task(pending_refresh())
        await entered.wait()
        await asyncio.sleep(0.05)
        assert not pending.done()
        row.google_connected = False
        row.google_account_version += 1
        await disconnect_session.commit()
        with pytest.raises(ApiError) as exc:
            await asyncio.wait_for(pending, timeout=5)
    assert exc.value.code == "reauth_required"
    assert token
