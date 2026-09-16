"""Bounded Google OAuth REST adapter. Never surface provider bodies or credentials."""

from dataclasses import dataclass

import httpx

from app.config import get_settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = ["openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
]


class GoogleAuthError(Exception):
    def __init__(self, message: str, status: int | None = None, *, revoked=False):
        super().__init__(message)
        self.message, self.status, self.revoked = message, status, revoked


@dataclass(repr=False)
class GoogleTokens:
    access_token: str
    expires_in: int
    refresh_token: str | None
    granted_scopes: list[str] | None


@dataclass
class GoogleUser:
    sub: str
    email: str
    name: str | None
    email_verified: bool


def _parse_json(response):
    try:
        body = response.json()
    except ValueError:
        raise GoogleAuthError("Invalid Google response.") from None
    if not isinstance(body, dict):
        raise GoogleAuthError("Invalid Google response.")
    return body


async def _request(method, url, *, transport=None, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise GoogleAuthError("Google is temporarily unavailable.") from None
    if response.status_code != 200:
        # Only invalid_grant establishes refresh-token revocation; a 400 can also
        # indicate a deployment/client configuration error. Never log the body.
        try:
            body = response.json()
        except ValueError:
            body = None
        revoked = isinstance(body, dict) and body.get("error") == "invalid_grant"
        raise GoogleAuthError(
            "Google authorization request failed.", response.status_code, revoked=revoked
        )
    return _parse_json(response)


def _tokens(body):
    access, refresh = body.get("access_token"), body.get("refresh_token")
    expiry, scope = body.get("expires_in"), body.get("scope")
    if (
        not isinstance(access, str)
        or not access
        or len(access) > 16384
        or (refresh is not None and (not isinstance(refresh, str) or not refresh))
        or type(expiry) is not int
        or not 0 < expiry <= 86400
        or (scope is not None and not isinstance(scope, str))
    ):
        raise GoogleAuthError("Invalid Google token response.")
    return GoogleTokens(
        access, expiry, refresh, sorted(set(scope.split())) if scope is not None else None
    )


async def exchange_code(code, redirect_uri, code_verifier=None, *, transport=None):
    settings = get_settings()
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    return _tokens(await _request("POST", TOKEN_URL, transport=transport, data=data))


async def refresh_access_token(refresh_token, *, transport=None):
    settings = get_settings()
    return _tokens(
        await _request(
            "POST",
            TOKEN_URL,
            transport=transport,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    )


async def fetch_userinfo(access_token, *, transport=None):
    body = await _request(
        "GET",
        USERINFO_URL,
        transport=transport,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    sub, email, name = body.get("sub"), body.get("email"), body.get("name")
    if (
        not isinstance(sub, str)
        or not 0 < len(sub) <= 64
        or not isinstance(email, str)
        or not 0 < len(email) <= 320
        or "@" not in email
        or any(c in email for c in "\r\n\x00")
        or (name is not None and (not isinstance(name, str) or len(name) > 200))
        or body.get("email_verified") is not True
    ):
        raise GoogleAuthError("Google account identity is not verified.")
    return GoogleUser(sub, email, name, True)
