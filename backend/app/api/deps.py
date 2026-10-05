"""Shared FastAPI dependencies: a JWT is valid only for its live Google connection."""

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.config import Settings, get_settings
from app.db.engine import get_session
from app.db.models import User


def settings_dep() -> Settings:
    return get_settings()


@dataclass(frozen=True)
class AuthenticatedSession:
    user_id: int
    account_version: int
    session_version: int
    refresh_expires_at: int | None = None


async def get_current_session(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(settings_dep)] = None,
) -> AuthenticatedSession:
    return await _authenticate(session, authorization, settings, allow_refresh=False)


async def get_renewal_session(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(settings_dep)] = None,
) -> AuthenticatedSession:
    return await _authenticate(session, authorization, settings, allow_refresh=True)


async def _authenticate(session, authorization, settings, *, allow_refresh):
    """Always verify signature, expiry and revocation; renewal is route-scoped."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Missing bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "token_expired", "Session expired — refresh and retry.") from exc
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError) as exc:
        raise ApiError(401, "unauthorized", "Invalid token.") from exc
    use = payload.get("token_use")
    if (use is not None and use not in ("access", "refresh")) or (
        use == "refresh" and not allow_refresh
    ):
        raise ApiError(401, "unauthorized", "Use an access token for this request.")
    try:
        user_id = int(payload["sub"])
        if user_id <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ApiError(401, "unauthorized", "Invalid token subject.") from None
    # Existing JWTs have no generations. They cannot be safely revoked, so
    # deployment of this contract deliberately requires one fresh Google sign-in.
    account_version = payload.get("av")
    session_version = payload.get("sv")
    if (
        type(account_version) is not int
        or account_version < 1
        or type(session_version) is not int
        or session_version < 1
    ):
        raise ApiError(401, "reauth_required", "Sign in with Google again.")
    if type(payload.get("exp")) is not int:
        raise ApiError(401, "unauthorized", "Invalid token expiry.")
    deadline = payload["exp"] if use == "refresh" else payload.get("session_exp")
    if deadline is not None and (type(deadline) is not int or payload["exp"] > deadline):
        raise ApiError(401, "unauthorized", "Invalid session deadline.")
    row = await session.execute(
        select(
            User.google_account_version, User.threadly_session_version, User.google_connected
        ).where(User.id == user_id)
    )
    current = row.one_or_none()
    if (
        current is None
        or not current.google_connected
        or current.google_account_version != account_version
        or current.threadly_session_version != session_version
    ):
        raise ApiError(401, "reauth_required", "Sign in with Google again.")
    return AuthenticatedSession(
        user_id,
        account_version,
        session_version,
        deadline,
    )


async def get_current_user_id(
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> int:
    return authenticated.user_id


CurrentUser = Annotated[int, Depends(get_current_user_id)]
CurrentSession = Annotated[AuthenticatedSession, Depends(get_current_session)]

RenewalSession = Annotated[AuthenticatedSession, Depends(get_renewal_session)]
