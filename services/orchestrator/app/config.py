import os

DEV_MODE = os.getenv("THREADLY_DEV_MODE", "false").lower() == "true"
DATABASE_URL = os.getenv("THREADLY_DATABASE_URL", "postgresql://threadly:threadly@postgres:5432/threadly")
INTERNAL_TOKEN = os.getenv("THREADLY_INTERNAL_TOKEN", "dev-internal")
INFERENCE_URL = os.getenv("THREADLY_INFERENCE_URL", "http://inference:8020")
CHROMA_HOST = os.getenv("THREADLY_CHROMA_HOST", "chroma")
CHROMA_PORT = int(os.getenv("THREADLY_CHROMA_PORT", "8000"))
PROMPTS_PATH = os.getenv("THREADLY_PROMPTS_PATH", "/models/prompts.yaml")

DEFAULT_ENTITY_WINDOW_DAYS = int(os.getenv("THREADLY_ENTITY_WINDOW_DAYS", "60"))
ENTITY_PAGE_SIZE = int(os.getenv("THREADLY_ENTITY_PAGE_SIZE", "20"))
RAG_TOP_K = int(os.getenv("THREADLY_RAG_TOP_K", "3"))
RAG_MAX_DOCS_PER_USER = int(os.getenv("THREADLY_RAG_MAX_DOCS_PER_USER", "500"))
RAG_CHAR_CAP = int(os.getenv("THREADLY_RAG_CHAR_CAP", "3000"))
THREAD_CONTEXT_CHAR_CAP = int(os.getenv("THREADLY_THREAD_CONTEXT_CHAR_CAP", "6000"))
