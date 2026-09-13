"""Module 7 — RAG SERVICE (build: W3).

Purpose: writing-voice personalisation. We embed the user's SENT mail into a
per-user chroma collection and retrieve the k most relevant examples when
drafting, capped at 3000 chars total so the 4b prompt stays inside budget.

chromadb is imported lazily — app must boot without it installed.
"""
from app.config import get_settings

RETRIEVE_CHAR_CAP = 3000


def _client():
    import chromadb  # lazy: heavy SDK

    settings = get_settings()
    host_port = settings.chroma_url.removeprefix("http://").rsplit(":", 1)
    return chromadb.HttpClient(host=host_port[0], port=int(host_port[1]))


def collection_name(user_id: int) -> str:
    return f"user_{user_id}_sent"


async def index_sent_message(user_id: int, msg_id: str, body_clean: str) -> None:
    """TODO(W3): embed + upsert into the user's collection (capped size — t3 memory)."""
    raise NotImplementedError


async def retrieve(user_id: int, query: str, k: int = 5) -> list[str]:
    """TODO(W3): query collection, trim results to RETRIEVE_CHAR_CAP total chars."""
    raise NotImplementedError
