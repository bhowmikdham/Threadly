"""Shared FastAPI dependencies: settings, DB session, current user (JWT check)."""
from typing import Annotated

import jwt
from fastapi import Depends, Header

from app.api.errors import ApiError
from app.config import Settings, get_settings


def settings_dep() -> Settings:
    return get_settings()


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(settings_dep)] = None,
) -> int:
    """Validate the session JWT (module 1 responsibility: JWT check on every route)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Missing bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "token_expired", "Session expired — refresh and retry.") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "unauthorized", "Invalid token.") from exc
    return int(payload["sub"])


CurrentUser = Annotated[int, Depends(get_current_user_id)]
