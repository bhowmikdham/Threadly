"""Google OAuth + userinfo over plain REST (module 2).

We deliberately use httpx against Google's token endpoints instead of the
google-auth SDK stack: two POSTs and a GET, fully testable with a mock
transport, and it keeps ~100MB of SDK out of the api image.

Flow (extension side): chrome.identity.launchWebAuthFlow -> auth code ->
POST /auth/google/exchange {code, redirect_uri}. The client secret only ever
lives here, server-side.
"""
from dataclasses import dataclass

import httpx

from app.config import get_settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# gmail.readonly for sync, gmail.send for approved drafts (W3), openid basics for identity
SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GoogleAuthError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class GoogleTokens:
    access_token: str
    expires_in: int
    refresh_token: str | None  # only present on first consent (access_type=offline prompt=consent)
    id_claims: dict


@dataclass
class GoogleUser:
    sub: str
    email: str
    name: str | None


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, transport=transport)


async def exchange_code(
    code: str, redirect_uri: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> GoogleTokens:
    """Auth code -> access/refresh tokens. Raises GoogleAuthError on any non-200."""
    settings = get_settings()
    async with _client(transport) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if r.status_code != 200:
        raise GoogleAuthError(f"code exchange failed: {r.text[:200]}", r.status_code)
    body = r.json()
    return GoogleTokens(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in", 3600)),
        refresh_token=body.get("refresh_token"),
        id_claims={},
    )


async def refresh_access_token(
    refresh_token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> GoogleTokens:
    settings = get_settings()
    async with _client(transport) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    if r.status_code != 200:
        # invalid_grant => user revoked access; caller must force re-auth
        raise GoogleAuthError(f"refresh failed: {r.text[:200]}", r.status_code)
    body = r.json()
    return GoogleTokens(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in", 3600)),
        refresh_token=body.get("refresh_token"),
        id_claims={},
    )


async def fetch_userinfo(
    access_token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> GoogleUser:
    async with _client(transport) as client:
        r = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        raise GoogleAuthError(f"userinfo failed: {r.text[:200]}", r.status_code)
    body = r.json()
    return GoogleUser(sub=body["sub"], email=body.get("email", ""), name=body.get("name"))
