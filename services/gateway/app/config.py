import os

DEV_MODE = os.getenv("THREADLY_DEV_MODE", "false").lower() == "true"
DATABASE_URL = os.getenv("THREADLY_DATABASE_URL", "postgresql://threadly:threadly@postgres:5432/threadly")
JWT_SECRET = os.getenv("THREADLY_JWT_SECRET", "dev-secret")
INTERNAL_TOKEN = os.getenv("THREADLY_INTERNAL_TOKEN", "dev-internal")

ORCHESTRATOR_URL = os.getenv("THREADLY_ORCHESTRATOR_URL", "http://orchestrator:8010")
GMAIL_SERVICE_URL = os.getenv("THREADLY_GMAIL_SERVICE_URL", "http://gmail:8030")

ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("THREADLY_ACCESS_TTL", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("THREADLY_REFRESH_TTL", str(30 * 24 * 3600)))

DEFAULT_ENTITY_WINDOW_DAYS = int(os.getenv("THREADLY_ENTITY_WINDOW_DAYS", "60"))
ENTITY_PAGE_SIZE = int(os.getenv("THREADLY_ENTITY_PAGE_SIZE", "20"))

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("THREADLY_ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
