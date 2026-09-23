"""Validate staging settings without printing secrets or calling providers."""

from app.config import get_settings
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url


def main():
    settings = get_settings()
    if settings.app_env != "prod":
        raise SystemExit("Staging requires APP_ENV=prod; development auth is not permitted.")
    if len(settings.secret_key) < 32 or settings.secret_key.startswith("dev-insecure"):
        raise SystemExit("Configure a strong SECRET_KEY.")
    try:
        Fernet(settings.fernet_key.encode())
    except (ValueError, TypeError):
        raise SystemExit("Configure a valid FERNET_KEY.") from None
    url = make_url(settings.database_url)
    if url.host != "postgres" or url.drivername != "postgresql+asyncpg":
        raise SystemExit("DATABASE_URL must target this stack's postgres service via asyncpg.")
    if settings.inference_provider != "bedrock":
        raise SystemExit("This staging deployment requires INFERENCE_PROVIDER=bedrock.")
    if settings.gmail_source_mode != "on_demand" or settings.mailbox_background_sync_enabled:
        raise SystemExit("Staging requires GMAIL_SOURCE_MODE=on_demand and MAILBOX_BACKGROUND_SYNC_ENABLED=false.")
    print("Gmail on-demand reads configured; mailbox replication disabled.")
    from app.workflows import auxiliary, registry
    registry.load_manifest()
    auxiliary.load_manifest()
    if settings.email_writes_enabled or settings.calendar_writes_enabled:
        if not settings.write_pilot_user_ids_values:
            raise SystemExit("Writes require an explicit WRITE_PILOT_USER_IDS allowlist.")
        if (settings.email_writes_enabled and not settings.email_reconciliation_enabled) or (
            settings.calendar_writes_enabled and not settings.calendar_reconciliation_enabled
        ):
            raise SystemExit("Enable the matching read reconciliation control before pilot writes.")
        print("Pilot-only writes configured; each action still requires exact user approval.")
    else:
        print("External writes disabled; previews and reviewed workflows remain available.")
    if settings.bedrock_model_id:
        print("Bedrock model configured; access and generation still require a separate smoke test.")
    else:
        print("PENDING: Bedrock model selection. Do not submit generation jobs yet.")
    if not all((settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)):
        print("PENDING: Google OAuth configuration. Live Gmail/Calendar login is unavailable.")
    print("Configuration preflight passed; no provider calls made.")


if __name__ == "__main__":
    main()
