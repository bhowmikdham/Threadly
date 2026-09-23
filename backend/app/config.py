"""Central settings — every env var the stack reads, in one typed place.

Values come from the environment (compose injects repo-root .env via env_file).
Defaults are dev-safe placeholders; prod MUST override the obvious ones.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # app
    app_env: str = "dev"  # dev | prod
    app_version: str = "0.1.0"
    secret_key: str = "dev-insecure-change-me-needs-32-bytes!"  # override in prod (.env)
    jwt_ttl_minutes: int = 1440
    jwt_algorithm: str = "HS256"

    gmail_source_mode: Literal["on_demand", "legacy_sync"] = "on_demand"
    mailbox_background_sync_enabled: bool = False
    mailbox_sync_max_messages: int = Field(default=5000, ge=100, le=100000)

    assistant_disabled_intents: str = ""

    @property
    def assistant_disabled_intents_values(self) -> set[str]:
        return {
            value.strip() for value in self.assistant_disabled_intents.split(",") if value.strip()
        }

    @field_validator("assistant_disabled_intents")
    @classmethod
    def known_disabled_intents(cls, value):
        labels = {v.strip() for v in value.split(",") if v.strip()}
        if labels - {"summarise", "plan_schedule", "reply", "compose", "other"}:
            raise ValueError("Unknown disabled intent")
        return value

    # data stores
    database_url: str = "postgresql+asyncpg://threadly:change-me@postgres:5432/threadly"
    chroma_url: str = "http://chroma:8000"

    # google oauth + gmail (test mode, <=100 users)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_redirect_uri_allowlist: str = ""
    # Explicit domain-free staging exception; only the fixed local test callback.
    google_allow_loopback_test_callback: bool = False
    fernet_key: str = ""  # encrypts refresh tokens at rest (auth/crypto.py)

    # Explicit migration switch; Bedrock never falls back to another cloud.
    inference_provider: Literal["legacy", "bedrock"] = "legacy"
    bedrock_region: str = "ap-southeast-2"
    bedrock_model_id: str = ""
    bedrock_small_model_id: str = ""
    bedrock_read_timeout_s: int = Field(default=90, ge=1, le=300)

    # Optional JSON registry. Empty preserves native task acceptance. Never use DRAFT aliases.
    assistant_workflow_manifest: str = ""
    assistant_auxiliary_workflow_manifest: str = ""

    # Separate exact-approval workers; real writes require explicit pilot membership.
    calendar_writes_enabled: bool = False
    calendar_reconciliation_enabled: bool = False
    write_pilot_user_ids: str = ""

    @property
    def write_pilot_user_ids_values(self) -> set[str]:
        return {
            value.strip()
            for value in self.write_pilot_user_ids.split(",")
            if value.strip().isdigit()
        }

    @field_validator("write_pilot_user_ids")
    @classmethod
    def valid_pilot_ids(cls, value):
        if any(
            not part.strip().isdigit() or int(part.strip()) < 1
            for part in value.split(",")
            if part.strip()
        ):
            raise ValueError("Pilot identifiers must be positive local user IDs")
        return value

    email_writes_enabled: bool = False
    email_reconciliation_enabled: bool = False
    email_action_lease_seconds: int = Field(default=120, ge=60, le=300)

    # Legacy rollback path (superseded by ADR 003).
    ollama_base_url: str = "http://localhost:11434"
    model_main: str = "qwen3.5:4b-threadly"
    model_small: str = "qwen3.5:2b"
    openrouter_api_key: str = ""
    model_fallback: str = "qwen/qwen3.5-9b"
    model_health_timeout_s: float = 2.0

    # voice (keys server-side only — never shipped to the extension)
    elevenlabs_api_key: str = ""

    # ml artifacts mount (see ml/README.md)
    ml_dir: str = "/ml"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def google_redirect_uri_allowlist_values(self) -> set[str]:
        configured = self.google_redirect_uri_allowlist.split(",")
        configured.append(self.google_redirect_uri)
        return {uri.strip() for uri in configured if uri.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
