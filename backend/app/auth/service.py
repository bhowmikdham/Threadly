"""Module 2 — AUTH SERVICE (W1, implemented).

OAuth code exchange + refresh against Google, session JWT issuance, and
encrypted persistence of tokens (crypto.py). Client secret and all Google
tokens live server-side only; the extension holds just the session JWT.
"""
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.auth import crypto, google
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import User

# refresh the google access token this long before it actually expires
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
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, User]:
    """Full login: code -> google tokens -> userinfo -> upsert user -> session JWT."""
    try:
        tokens = await google.exchange_code(code, redirect_uri, transport=transport)
        info = await google.fetch_userinfo(tokens.access_token, transport=transport)
    except google.GoogleAuthError as exc:
        raise ApiError(401, "oauth_exchange_failed", exc.message) from exc

    user = await repo.upsert_user(
        session,
        google_sub=info.sub,
        email=info.email,
        display_name=info.name,
        access_token_enc=crypto.encrypt_token(tokens.access_token),
        access_token_expires_at=datetime.now(UTC) + timedelta(seconds=tokens.expires_in),
        # google returns refresh_token only on first consent — never null an existing one
        refresh_token_enc=(
            crypto.encrypt_token(tokens.refresh_token) if tokens.refresh_token else None
        ),
    )
    await session.commit()
    return issue_session_jwt(user.id), user


async def get_valid_access_token(
    session: AsyncSession,
    user: User,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return a live Gmail access token, refreshing (and re-persisting) if stale."""
    now = datetime.now(UTC)
    if (
        user.access_token_enc is not None
        and user.access_token_expires_at is not None
        and user.access_token_expires_at.replace(tzinfo=UTC) - _EXPIRY_SLACK > now
    ):
        return crypto.decrypt_token(user.access_token_enc)

    if user.refresh_token_enc is None:
        raise ApiError(401, "reauth_required", "No refresh token on file — sign in again.")
    try:
        tokens = await google.refresh_access_token(
            crypto.decrypt_token(user.refresh_token_enc), transport=transport
        )
    except google.GoogleAuthError as exc:
        raise ApiError(401, "reauth_required", "Google session revoked — sign in again.") from exc

    user.access_token_enc = crypto.encrypt_token(tokens.access_token)
    user.access_token_expires_at = now + timedelta(seconds=tokens.expires_in)
    if tokens.refresh_token:
        user.refresh_token_enc = crypto.encrypt_token(tokens.refresh_token)
    await session.commit()
    return crypto.decrypt_token(user.access_token_enc)
