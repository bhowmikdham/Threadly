"""Google OAuth lifecycle and backend-owned encrypted credential handling."""

from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.auth import crypto, google
from app.config import get_settings
from app.db import repositories as repo
from app.db.engine import get_session_factory
from app.db.models import User

_EXPIRY_SLACK = timedelta(seconds=120)


def issue_session_jwt(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


async def exchange_code(
    session: AsyncSession,
    code: str,
    redirect_uri: str,
    *,
    code_verifier: str | None = None,
    expected_user_id: int | None = None,
    expected_version: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, User]:
    """Exchange verified Google identity data without committing the caller session."""
    try:
        tokens = await google.exchange_code(
            code,
            redirect_uri,
            code_verifier=code_verifier,
            transport=transport,
        )
        info = await google.fetch_userinfo(tokens.access_token, transport=transport)
    except google.GoogleAuthError as exc:
        raise ApiError(401, "oauth_exchange_failed", exc.message) from exc

    if not info.email or info.email_verified is not True:
        raise ApiError(401, "oauth_exchange_failed", "Google account email is not verified.")

    if expected_user_id is not None:
        current = await session.get(
            User, expected_user_id, with_for_update=True, populate_existing=True
        )
        if current is None or current.google_sub != info.sub:
            raise ApiError(409, "google_account_mismatch", "Reconnect the same Google account.")
        if current.google_account_version != expected_version:
            raise ApiError(409, "google_connection_changed", "Google connection changed; retry.")

    now = datetime.now(UTC)
    user = await repo.upsert_user(
        session,
        google_sub=info.sub,
        email=info.email,
        display_name=info.name,
        access_token_enc=crypto.encrypt_token(tokens.access_token),
        access_token_expires_at=now + timedelta(seconds=tokens.expires_in),
        refresh_token_enc=(
            crypto.encrypt_token(tokens.refresh_token) if tokens.refresh_token else None
        ),
        google_scopes=tokens.granted_scopes,
        google_identity={"sub": info.sub, "email": info.email, "name": info.name},
        email_verified=info.email_verified,
        google_connected=True,
        now=now,
    )
    return issue_session_jwt(user.id), user


async def get_valid_access_token(
    session: AsyncSession,
    user: User,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return a live token without committing unrelated caller-owned changes."""
    # Do not trust the caller's cached identity or flush/commit its pending work.
    # Future action callers must release their locks before entering this provider seam.
    async with get_session_factory()() as token_session:
        user = await token_session.scalar(select(User).where(User.id == user.id))
    if user is None or not user.google_connected:
        raise ApiError(401, "reauth_required", "Reconnect Google to continue.")

    now = datetime.now(UTC)
    expires_at = user.access_token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        user.access_token_enc is not None
        and expires_at is not None
        and expires_at - _EXPIRY_SLACK > now
    ):
        return crypto.decrypt_token(user.access_token_enc)

    if user.refresh_token_enc is None:
        raise ApiError(401, "reauth_required", "Reconnect Google to continue.")

    observed_version = user.google_token_version
    try:
        tokens = await google.refresh_access_token(
            crypto.decrypt_token(user.refresh_token_enc), transport=transport
        )
    except google.GoogleAuthError as exc:
        if exc.revoked:
            await disconnect_google_account(user.id, expected_token_version=observed_version)
            raise ApiError(401, "reauth_required", "Reconnect Google to continue.") from exc
        raise ApiError(
            503,
            "google_token_unavailable",
            "Google token refresh is temporarily unavailable.",
        ) from exc

    persisted = await _persist_refreshed_tokens(
        user.id,
        observed_version=observed_version,
        tokens=tokens,
        now=now,
    )
    if not persisted:
        raise ApiError(409, "google_connection_changed", "Google connection changed; retry.")
    return tokens.access_token


async def _persist_refreshed_tokens(
    user_id: int,
    *,
    observed_version: int,
    tokens: google.GoogleTokens,
    now: datetime,
) -> bool:
    """Persist a refresh in a dedicated transaction fenced by account version."""
    async with get_session_factory()() as refresh_session:
        user = await refresh_session.get(User, user_id, with_for_update=True)
        if (
            user is None
            or not user.google_connected
            or user.google_token_version != observed_version
        ):
            await refresh_session.rollback()
            return False

        user.access_token_enc = crypto.encrypt_token(tokens.access_token)
        user.access_token_expires_at = now + timedelta(seconds=tokens.expires_in)
        if tokens.refresh_token:
            user.refresh_token_enc = crypto.encrypt_token(tokens.refresh_token)
        if tokens.granted_scopes is not None and tokens.granted_scopes != user.google_scopes:
            user.google_scopes = tokens.granted_scopes
            user.google_account_version += 1
        user.google_token_version += 1
        await refresh_session.commit()
        return True


async def disconnect_google_account(
    user_id: int, *, expected_version: int | None = None, expected_token_version: int | None = None
) -> bool:
    """Remove locally usable credentials, fenced against a stale refresh result."""
    async with get_session_factory()() as disconnect_session:
        user = await disconnect_session.get(User, user_id, with_for_update=True)
        if (
            user is None
            or (expected_version is not None and user.google_account_version != expected_version)
            or (
                user is not None
                and expected_token_version is not None
                and user.google_token_version != expected_token_version
            )
        ):
            await disconnect_session.rollback()
            return False

        if not user.google_connected:
            return True
        user.google_connected = False
        user.google_connected_at = None
        user.access_token_enc = None
        user.access_token_expires_at = None
        user.refresh_token_enc = None
        user.google_account_version += 1
        user.google_token_version += 1
        await disconnect_session.commit()
        return True
