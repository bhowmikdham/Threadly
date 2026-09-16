"""Google connection readiness is separate from workflow implementation availability."""

from typing import Literal

from app.schemas.assistant import StrictModel


class GoogleAccountCapability(StrictModel):
    connected: bool
    email_verified: bool | None
    identity_source: Literal["google_userinfo", "unknown"]
    account_version: int
    credentials_available: bool
    readiness_evidence: Literal["stored_grants_and_credentials_not_live_probe"]


class GoogleCapability(StrictModel):
    id: Literal["gmail_read", "gmail_send", "calendar_read", "calendar_list", "calendar_write"]
    implemented: bool
    enabled: bool
    scope_status: Literal["unknown", "granted", "missing"]
    source: Literal["google_scope"]
    status: Literal[
        "ready",
        "reconnect_required",
        "not_implemented",
        "disabled",
        "scope_unknown",
        "scope_missing",
    ]
    ready: bool
    any_of_scopes: list[str]


class ReconnectCapability(StrictModel):
    available: bool
    method: Literal["POST /auth/google/reconnect"]
    requestable_capabilities: list[Literal["gmail_read", "calendar_read"]]
    state_pkce_required: bool


class GoogleCapabilities(StrictModel):
    account: GoogleAccountCapability
    capabilities: list[GoogleCapability]
    reconnect: ReconnectCapability
