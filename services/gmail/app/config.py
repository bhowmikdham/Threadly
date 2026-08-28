import os

DEV_MODE = os.getenv("THREADLY_DEV_MODE", "false").lower() == "true"
DATABASE_URL = os.getenv("THREADLY_DATABASE_URL", "postgresql://threadly:threadly@postgres:5432/threadly")
INTERNAL_TOKEN = os.getenv("THREADLY_INTERNAL_TOKEN", "dev-internal")
ORCHESTRATOR_URL = os.getenv("THREADLY_ORCHESTRATOR_URL", "http://orchestrator:8010")
INFERENCE_URL = os.getenv("THREADLY_INFERENCE_URL", "http://inference:8020")
TIER2_EXTRACTION = os.getenv("THREADLY_TIER2_EXTRACTION", "true").lower() == "true"
FERNET_KEY = os.getenv("THREADLY_FERNET_KEY", "")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

SYNC_INTERVAL_SECONDS = int(os.getenv("THREADLY_SYNC_INTERVAL_SECONDS", "300"))
FIRST_SYNC_LOOKBACK_DAYS = int(os.getenv("THREADLY_FIRST_SYNC_LOOKBACK_DAYS", "120"))
