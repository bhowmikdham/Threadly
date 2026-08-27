import os

DEV_MODE = os.getenv("THREADLY_DEV_MODE", "false").lower() == "true"
INTERNAL_TOKEN = os.getenv("THREADLY_INTERNAL_TOKEN", "dev-internal")

OLLAMA_BASE_URL = os.getenv("THREADLY_OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("THREADLY_OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_SMALL_MODEL = os.getenv("THREADLY_OLLAMA_SMALL_MODEL", "qwen3.5:2b")
OLLAMA_EMBED_MODEL = os.getenv("THREADLY_OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_HEALTH_TIMEOUT_SECONDS = float(os.getenv("THREADLY_OLLAMA_HEALTH_TIMEOUT", "2.0"))
OLLAMA_HEALTH_CACHE_SECONDS = float(os.getenv("THREADLY_OLLAMA_HEALTH_CACHE", "30"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("THREADLY_OPENROUTER_MODEL", "qwen/qwen3.5-9b")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

BERT_DIR = os.getenv("THREADLY_BERT_DIR", "/models/bert")
EMBED_DIM = int(os.getenv("THREADLY_STUB_EMBED_DIM", "384"))
MAX_TOKENS_CAP = int(os.getenv("THREADLY_MAX_TOKENS_CAP", "1024"))
