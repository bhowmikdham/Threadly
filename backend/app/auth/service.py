"""Module 2 — AUTH SERVICE (build: W1).

OAuth code exchange + refresh against Google (test mode, <=100 users), session
JWT issuance, and encrypted persistence of refresh tokens (crypto.py).

Flow: extension completes the OAuth consent -> POSTs the auth code to
/auth/google/exchange -> we exchange it server-side (client secret never leaves
here) -> upsert users row -> return a session JWT (sub = user id).
"""
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings


def issue_session_jwt(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


async def exchange_code(code: str) -> dict:
    """TODO(W1): google-auth-oauthlib exchange (lazy import), upsert user,
    encrypt+store refresh token, return {"jwt", "user"}."""
    raise NotImplementedError


async def refresh_google_access_token(user_id: int) -> str:
    """TODO(W1): decrypt stored refresh token, mint a fresh access token for Gmail calls."""
    raise NotImplementedError
