"""Eviction policy for the per-user RAG collection cap. Pure — unit-tested."""


def select_evictions(docs: list[tuple[str, float]], cap: int) -> list[str]:
    """docs: (doc_id, indexed_at epoch). Returns the ids to delete so that only
    the `cap` most recently indexed documents remain."""
    if cap <= 0 or len(docs) <= cap:
        return []
    ranked = sorted(docs, key=lambda d: d[1], reverse=True)
    return [doc_id for doc_id, _ in ranked[cap:]]
