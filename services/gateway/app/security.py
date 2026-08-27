import datetime as dt

import jwt
from fastapi import Header
from threadly_common.errors import APIError

from . import config


def _create_token(user_id: int, kind: str, ttl_seconds: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "kind": kind,
        "iat": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def create_session(user_id: int) -> dict:
    return {
        "access_token": _create_token(user_id, "access", config.ACCESS_TOKEN_TTL_SECONDS),
        "refresh_token": _create_token(user_id, "refresh", config.REFRESH_TOKEN_TTL_SECONDS),
        "token_type": "bearer",
        "expires_in": config.ACCESS_TOKEN_TTL_SECONDS,
    }


def decode_token(token: str, expected_kind: str) -> int:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise APIError("token_expired", "Token has expired.", status=401)
    except jwt.InvalidTokenError:
        raise APIError("invalid_token", "Token is invalid.", status=401)
    if payload.get("kind") != expected_kind:
        raise APIError("invalid_token", "Wrong token kind.", status=401)
    return int(payload["sub"])


async def current_user(authorization: str | None = Header(default=None)) -> int:
    """FastAPI dependency: resolves the JWT bearer to a user id."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError("unauthorized", "Missing bearer token.", status=401)
    return decode_token(authorization.split(" ", 1)[1], "access")


def internal_headers(user_id: int | None = None) -> dict:
    headers = {"X-Threadly-Internal": config.INTERNAL_TOKEN}
    if user_id is not None:
        headers["X-Threadly-User"] = str(user_id)
    return headers
