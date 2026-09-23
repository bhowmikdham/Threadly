"""One-use OAuth state bound to a client-held PKCE secret and exact callback."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from sqlalchemy import delete, func, select

from app.api.errors import ApiError
from app.auth.google import CALENDAR_SCOPES, SCOPES
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import GoogleOAuthSession, User


def challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def validate_redirect(uri):
    try:
        parsed = urlsplit(uri)
    except ValueError:
        raise ApiError(
            400, "invalid_redirect_uri", "The OAuth redirect URI is not allowed."
        ) from None
    settings = get_settings()
    local_test = (
        settings.google_allow_loopback_test_callback
        and uri == "http://127.0.0.1:8765/oauth/callback"
    )
    if (
        (parsed.scheme != "https" and not local_test)
        or not parsed.netloc
        or parsed.fragment
        or parsed.username
        or parsed.password
        or uri not in settings.google_redirect_uri_allowlist_values
    ):
        raise ApiError(400, "invalid_redirect_uri", "The OAuth redirect URI is not allowed.")


async def begin(
    session,
    redirect_uri,
    code_challenge,
    *,
    user_id=None,
    calendar_read=False,
    gmail_send=False,
    calendar_write=False,
):
    if calendar_read and user_id is None:
        raise ApiError(400, "calendar_login_required", "Sign in before connecting Calendar.")
    validate_redirect(redirect_uri)
    settings = get_settings()
    if (gmail_send or calendar_write) and str(user_id) not in settings.write_pilot_user_ids_values:
        raise ApiError(
            409, "write_pilot_unavailable", "Write access is limited to configured test accounts."
        )
    scopes = SCOPES + (CALENDAR_SCOPES if calendar_read or calendar_write else [])
    if gmail_send:
        scopes = scopes + ["https://www.googleapis.com/auth/gmail.send"]
    if calendar_write:
        scopes = scopes + ["https://www.googleapis.com/auth/calendar.events"]
    if not settings.google_client_id or not settings.google_client_secret:
        raise ApiError(503, "google_not_configured", "Google login is not configured.")
    user = await session.get(User, user_id) if user_id is not None else None
    if user_id is not None and user is None:
        raise ApiError(401, "unauthorized", "Unknown user.")
    now = await session.scalar(select(func.clock_timestamp()))
    # Bounded opportunistic cleanup; no credential or raw callback data in logs.
    old = (
        select(GoogleOAuthSession.state_hash)
        .where(GoogleOAuthSession.expires_at < now - timedelta(days=1))
        .order_by(GoogleOAuthSession.expires_at)
        .limit(100)
    )
    await session.execute(delete(GoogleOAuthSession).where(GoogleOAuthSession.state_hash.in_(old)))
    state = secrets.token_urlsafe(32)
    expires = now + timedelta(minutes=10)
    session.add(
        GoogleOAuthSession(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            user_id=user_id,
            account_version=user.google_account_version if user else None,
            expires_at=expires,
        )
    )
    await session.flush()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    return {
        "state": state,
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
        "expires_at": expires.isoformat(),
    }


async def consume(state, redirect_uri, verifier):
    """Commit consumption separately: provider failure/timeout must not resurrect state."""
    validate_redirect(redirect_uri)
    async with get_session_factory()() as session:
        row = await session.get(
            GoogleOAuthSession, hashlib.sha256(state.encode()).hexdigest(), with_for_update=True
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            row is None
            or row.consumed_at is not None
            or row.expires_at <= now
            or row.redirect_uri != redirect_uri
            or not secrets.compare_digest(row.code_challenge, challenge(verifier))
        ):
            raise ApiError(400, "oauth_state_invalid", "Restart Google sign-in.")
        row.consumed_at = now
        result = row.user_id, row.account_version
        await session.commit()
        return result
