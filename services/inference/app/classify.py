"""Intent classification, three tiers matching the architecture:
1. BERT — if the AI team's files are mounted and torch is installed
2. small-model JSON — the '2b JSON fallback' via whichever backend is up
3. keyword rules — always available, keeps dev mode deterministic
Same response shape either way; `source` says which tier answered."""
import json
import logging
import os
import re

from threadly_common.models import Intent

from . import config
from .backends import router

log = logging.getLogger(__name__)

DEFAULT_LABELS = [i.value for i in Intent if i != Intent.UNKNOWN]

_bert_pipeline = None
_bert_checked = False


def _load_bert():
    global _bert_pipeline, _bert_checked
    if _bert_checked:
        return _bert_pipeline
    _bert_checked = True
    if not os.path.isdir(config.BERT_DIR):
        log.info("no BERT dir at %s; using fallback classifiers", config.BERT_DIR)
        return None
    try:
        from transformers import pipeline  # optional dep (requirements-bert.txt)
        _bert_pipeline = pipeline("text-classification", model=config.BERT_DIR, top_k=1)
        log.info("loaded BERT classifier from %s", config.BERT_DIR)
    except ImportError:
        log.warning("models/bert exists but transformers is not installed; rebuild with WITH_BERT=1")
    except Exception:
        log.exception("failed to load BERT from %s", config.BERT_DIR)
    return _bert_pipeline


async def _llm_classify(text: str, labels: list[str]) -> dict | None:
    """One small-model call, strict JSON out. Any failure returns None."""
    backend = await router.pick()
    if backend.name == "stub":
        return None
    prompt = (
        "Classify the user's message into exactly one label.\n"
        f"Labels: {', '.join(labels)}\n"
        f"Message: {text[:500]}\n"
        'Respond with ONLY this JSON, nothing else: {"label": "<one label>"}'
    )
    try:
        chunks = []
        async for token in backend.generate(prompt, system=None, max_tokens=32, small=True):
            chunks.append(token)
        raw = "".join(chunks)
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        label = json.loads(match.group(0)).get("label", "").strip()
        if label in labels:
            return {"label": label, "confidence": 0.7, "source": "llm"}
    except Exception:
        log.exception("LLM classify fallback failed")
    return None


KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (Intent.SUMMARISE.value, ["summar", "tldr", "tl;dr", "recap", "catch me up"]),
    (Intent.DRAFT.value, ["draft", "compose", "reply", "respond", "write back"]),
    (Intent.FETCH_ENTITY.value, ["order", "tracking", "invoice", "receipt", "refund", "booking", "commitment", "promised", "deadline"]),
]


async def classify(text: str, labels: list[str] | None = None) -> dict:
    labels = labels or DEFAULT_LABELS

    bert = _load_bert()
    if bert is not None:
        [result] = bert(text[:512])
        top = result[0] if isinstance(result, list) else result
        if top["label"] in labels:
            return {"label": top["label"], "confidence": float(top["score"]), "source": "bert"}

    if verdict := await _llm_classify(text, labels):
        return verdict

    lower = text.lower()
    for label, keywords in KEYWORD_RULES:
        if label in labels and any(kw in lower for kw in keywords):
            return {"label": label, "confidence": 0.6, "source": "rules"}
    fallback = Intent.SEARCH.value if Intent.SEARCH.value in labels else labels[0]
    return {"label": fallback, "confidence": 0.3, "source": "rules"}
