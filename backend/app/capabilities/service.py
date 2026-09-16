"""Compute client-facing capabilities without inferring unrecorded OAuth grants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models import User

GMAIL_READ_SCOPES = {
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
}
GMAIL_SEND_SCOPES = {
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
}
CALENDAR_READ_SCOPES = {
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar",
}
CALENDAR_LIST_SCOPES = {
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar",
}
CALENDAR_WRITE_SCOPES = {
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar",
}


def build_capabilities(user: User) -> dict[str, Any]:
    """Return readiness based on implementation state and persisted OAuth evidence."""
    expiry = user.access_token_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    credentials_available = bool(
        user.refresh_token_enc
        or (
            user.access_token_enc and expiry and expiry > datetime.now(UTC) + timedelta(seconds=120)
        )
    )
    verified_connection = bool(
        user.google_connected and user.google_email_verified and credentials_available
    )
    scopes = set(user.google_scopes) if user.google_scopes is not None else None
    return {
        "account": {
            "connected": bool(user.google_connected),
            "email_verified": user.google_email_verified,
            "identity_source": "google_userinfo" if user.google_identity else "unknown",
            "account_version": user.google_account_version,
            "credentials_available": credentials_available,
            "readiness_evidence": "stored_grants_and_credentials_not_live_probe",
        },
        "capabilities": [
            _capability(
                "gmail_read",
                implemented=True,
                enabled=True,
                connected=verified_connection,
                scopes=scopes,
                required_scopes=GMAIL_READ_SCOPES,
            ),
            _capability(
                "gmail_send",
                implemented=False,
                enabled=False,
                connected=verified_connection,
                scopes=scopes,
                required_scopes=GMAIL_SEND_SCOPES,
            ),
            _capability(
                "calendar_read",
                implemented=True,
                enabled=True,
                connected=verified_connection,
                scopes=scopes,
                required_scopes=CALENDAR_READ_SCOPES,
            ),
            _capability(
                "calendar_list",
                implemented=True,
                enabled=True,
                connected=verified_connection,
                scopes=scopes,
                required_scopes=CALENDAR_LIST_SCOPES,
            ),
            _capability(
                "calendar_write",
                implemented=False,
                enabled=False,
                connected=verified_connection,
                scopes=scopes,
                required_scopes=CALENDAR_WRITE_SCOPES,
            ),
        ],
        "reconnect": {
            "available": True,
            "method": "POST /auth/google/reconnect",
            "requestable_capabilities": ["gmail_read", "calendar_read"],
            "state_pkce_required": True,
        },
    }


def _capability(
    capability_id: str,
    *,
    implemented: bool,
    enabled: bool,
    connected: bool,
    scopes: set[str] | None,
    required_scopes: set[str],
) -> dict[str, Any]:
    if scopes is None:
        scope_status = "unknown"
    elif scopes.intersection(required_scopes):
        scope_status = "granted"
    else:
        scope_status = "missing"

    if not connected:
        status = "reconnect_required"
    elif not implemented:
        status = "not_implemented"
    elif not enabled:
        status = "disabled"
    elif scope_status == "granted":
        status = "ready"
    elif scope_status == "unknown":
        status = "scope_unknown"
    else:
        status = "scope_missing"

    return {
        "id": capability_id,
        "implemented": implemented,
        "enabled": enabled,
        "scope_status": scope_status,
        "source": "google_scope",
        "status": status,
        "ready": status == "ready",
        "any_of_scopes": sorted(required_scopes),
    }
