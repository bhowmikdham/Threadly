"""Central settings — every env var the stack reads, in one typed place.

Values come from the environment (compose injects repo-root .env via env_file).
Defaults are dev-safe placeholders; prod MUST override the obvious ones.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # app
    app_env: str = "dev"  # dev | prod
    app_version: str = "0.1.0"
    secret_key: str = "dev-insecure-change-me-needs-32-bytes!"  # override in prod (.env)
    jwt_ttl_minutes: int = 1440
    jwt_algorithm: str = "HS256"

    # data stores
    database_url: str = "postgresql+asyncpg://threadly:change-me@postgres:5432/threadly"
    chroma_url: str = "http://chroma:8000"

    # google oauth + gmail (test mode, <=100 users)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    fernet_key: str = ""  # encrypts refresh tokens at rest (auth/crypto.py)

    # Explicit migration switch; Bedrock never falls back to another cloud.
    inference_provider: Literal["legacy", "bedrock"] = "legacy"
    bedrock_region: str = "ap-southeast-2"
    bedrock_model_id: str = ""
    bedrock_small_model_id: str = ""
    bedrock_read_timeout_s: int = Field(default=90, ge=1, le=300)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
