"""Module 6 — EXTRACTOR (build: W2).

Two tiers, run at sync time (not query time):
- tier 1: regex over body_clean (patterns.py) — expected to cover 90%+
- tier 2: LLM on the residue only, constrained JSON, small model

Output lands in entities with UNIQUE (user_id, type, key) — upsert on conflict,
keep source_msg_id provenance so the UI can link back to the email.
"""


async def extract_tier1(user_id: int, msg_id: str, body_clean: str) -> list[dict]:
    """TODO(W2): run PATTERNS, normalise values, return candidate entity dicts."""
    raise NotImplementedError


async def extract_tier2(user_id: int, msg_id: str, body_clean: str) -> list[dict]:
    """TODO(W2): only called when tier 1 yields nothing for a message the
    classifier marked entity-rich. model_client.generate_json on model_small."""
    raise NotImplementedError
