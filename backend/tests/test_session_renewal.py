"""Persistent-login renewal is bounded, revocable and unusable for application APIs."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth import service
from app.config import get_settings
from tests.conftest import needs_pg
from tests.test_revocable_sessions import bearer, login

pytestmark = needs_pg


def claims(token):
    s = get_settings()
    return jwt.decode(token, s.secret_key, algorithms=[s.jwt_algorithm])


def signed(payload):
    s = get_settings()
    return jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm)


async def test_expired_access_renews_without_google_and_preserves_fixed_deadline(
    db_sessionmaker, secure_db_client
):
    access, user = await login(db_sessionmaker, sub="persist", email="persist@example.test")
    refresh = service.refresh_token_for(access)
    payload = claims(access)
    deadline = payload["session_exp"]
    payload["exp"] = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
    expired = signed(payload)
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(expired)).status_code == 401
    )
    assert secure_db_client.post("/auth/refresh", headers=bearer(expired)).status_code == 401
    for credential in (refresh, access):
        renewed = secure_db_client.post("/auth/refresh", headers=bearer(credential))
        assert renewed.status_code == 200, renewed.text
        assert renewed.headers["cache-control"] == "no-store"
        result = renewed.json()
        assert claims(result["jwt"])["session_exp"] == deadline
        assert claims(result["jwt"])["exp"] <= deadline
        assert claims(result["refresh_token"])["exp"] == deadline
        assert claims(result["refresh_token"])["token_use"] == "refresh"
        assert (
            secure_db_client.get(
                "/assistant/capabilities", headers=bearer(result["jwt"])
            ).status_code
            == 200
        )
        refresh = result["refresh_token"]
    assert claims(refresh)["sub"] == str(user.id)


async def test_refresh_credential_is_rejected_by_application_and_google_routes(
    db_sessionmaker, secure_db_client
):
    access, _ = await login(db_sessionmaker, sub="limited", email="limited@example.test")
    refresh = service.refresh_token_for(access)
    for method, path in (
        ("get", "/assistant/capabilities"),
        ("get", "/calendar/preferences"),
        ("post", "/auth/google/disconnect"),
        ("post", "/auth/google/reconnect"),
    ):
        response = getattr(secure_db_client, method)(path, headers=bearer(refresh))
        assert response.status_code == 401, (path, response.text)
    invalid = signed({**claims(refresh), "token_use": ["refresh"]})
    assert secure_db_client.post("/auth/refresh", headers=bearer(invalid)).status_code == 401


@pytest.mark.parametrize("action", ["logout", "disconnect", "relogin"])
async def test_renewal_revoked_by_session_or_connection_change(
    db_sessionmaker, secure_db_client, monkeypatch, action
):
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    access, user = await login(db_sessionmaker, sub="revoked", email="revoked@example.test")
    refresh = service.refresh_token_for(access)
    if action == "relogin":
        await login(db_sessionmaker, sub="revoked", email="revoked@example.test")
    else:
        path = "/auth/logout" if action == "logout" else "/auth/google/disconnect"
        # Sign-out must work after access expiry via the still-valid renewal token.
        credential = refresh if action == "logout" else access
        response = secure_db_client.post(path, headers=bearer(credential))
        assert response.status_code == 200, response.text
    result = secure_db_client.post("/auth/refresh", headers=bearer(refresh))
    assert result.status_code == 401 and result.json()["error"]["code"] == "reauth_required"
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(access)).status_code == 401
    )


async def test_expired_tampered_missing_and_deleted_renewal_credentials_fail_closed(
    db_sessionmaker, secure_db_client
):
    access, user = await login(db_sessionmaker, sub="invalid", email="invalid@example.test")
    refresh = service.refresh_token_for(access)
    payload = claims(refresh)
    expired = signed({**payload, "exp": int(datetime.now(UTC).timestamp()) - 1})
    missing_exp = signed({k: v for k, v in payload.items() if k != "exp"})
    tampered = refresh.rsplit(".", 1)[0] + ".invalid-signature"
    for bad in (expired, missing_exp, tampered):
        assert secure_db_client.post("/auth/refresh", headers=bearer(bad)).status_code == 401
    from app.db.models import User

    async with db_sessionmaker.begin() as session:
        await session.delete(await session.get(User, user.id))
    assert secure_db_client.post("/auth/refresh", headers=bearer(refresh)).status_code == 401


async def test_access_lifetime_cannot_outlive_renewal_deadline(db_sessionmaker, secure_db_client):
    _, user = await login(db_sessionmaker, sub="deadline", email="deadline@example.test")
    deadline = int(datetime.now(UTC).timestamp()) + 60
    refresh = service.issue_refresh_jwt(
        user.id, user.google_account_version, user.threadly_session_version, expires_at=deadline
    )
    result = secure_db_client.post("/auth/refresh", headers=bearer(refresh))
    assert result.status_code == 200
    assert claims(result.json()["jwt"])["exp"] == deadline
    # Refreshing with access instead cannot extend the original renewal deadline.
    second = secure_db_client.post("/auth/refresh", headers=bearer(result.json()["jwt"]))
    assert claims(second.json()["refresh_token"])["exp"] == deadline
