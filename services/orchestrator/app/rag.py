"""RAG over the user's sent mail: per-user Chroma collection, embeddings via
the inference service, retrieval capped by characters (t3 memory budget)."""
import asyncio
import logging
import time

import chromadb

from . import config, inference_client
from .rag_evict import select_evictions

log = logging.getLogger(__name__)

_client = None  # chromadb.HttpClient is a factory function, not a class


def _chroma():
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=config.CHROMA_HOST, port=config.CHROMA_PORT)
    return _client


def _collection_name(user_id: int) -> str:
    return f"user_{user_id}_sent"


async def index_documents(user_id: int, docs: list[dict]) -> int:
    """docs: [{"id": gmail_msg_id, "text": body}] — upserted so re-sync is safe."""
    docs = [d for d in docs if d.get("text")]
    if not docs:
        return 0
    embeddings = await inference_client.embed([d["text"][:2000] for d in docs])

    def _upsert():
        col = _chroma().get_or_create_collection(_collection_name(user_id))
        now = time.time()
        col.upsert(
            ids=[str(d["id"]) for d in docs],
            embeddings=embeddings,
            documents=[d["text"][:2000] for d in docs],
            metadatas=[{"indexed_at": now} for _ in docs],
        )
        # Per-user cap (t3 memory budget): evict the oldest-indexed docs.
        if col.count() > config.RAG_MAX_DOCS_PER_USER:
            got = col.get(include=["metadatas"])
            stamped = [
                (doc_id, (meta or {}).get("indexed_at", 0.0))
                for doc_id, meta in zip(got["ids"], got["metadatas"])
            ]
            evict = select_evictions(stamped, config.RAG_MAX_DOCS_PER_USER)
            if evict:
                col.delete(ids=evict)
                log.info("RAG cap: evicted %d docs for user %s", len(evict), user_id)

    await asyncio.to_thread(_upsert)
    return len(docs)


async def retrieve(user_id: int, query: str, k: int | None = None) -> list[str]:
    k = k or config.RAG_TOP_K
    try:
        [embedding] = await inference_client.embed([query])

        def _query():
            col = _chroma().get_or_create_collection(_collection_name(user_id))
            if col.count() == 0:
                return []
            res = col.query(query_embeddings=[embedding], n_results=min(k, col.count()))
            return res["documents"][0] if res["documents"] else []

        documents = await asyncio.to_thread(_query)
    except Exception:
        log.exception("RAG retrieval failed; drafting without style examples")
        return []

    # Cap total characters so the prompt fits the small models.
    out, budget = [], config.RAG_CHAR_CAP
    for doc in documents:
        if budget <= 0:
            break
        out.append(doc[:budget])
        budget -= len(doc)
    return out
