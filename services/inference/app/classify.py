"""Intent classification: BERT if the AI team's files are mounted and torch is
installed, keyword rules otherwise. Same response shape either way."""
import logging
import os

from threadly_common.models import Intent

from . import config

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
        log.info("no BERT dir at %s; using keyword-rule classifier", config.BERT_DIR)
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


KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (Intent.SUMMARISE.value, ["summar", "tldr", "tl;dr", "recap", "catch me up"]),
    (Intent.DRAFT.value, ["draft", "compose", "reply", "respond", "write back"]),
    (Intent.FETCH_ENTITY.value, ["order", "tracking", "invoice", "receipt", "refund", "booking"]),
]


def classify(text: str, labels: list[str] | None = None) -> dict:
    labels = labels or DEFAULT_LABELS

    bert = _load_bert()
    if bert is not None:
        [result] = bert(text[:512])
        top = result[0] if isinstance(result, list) else result
        if top["label"] in labels:
            return {"label": top["label"], "confidence": float(top["score"]), "source": "bert"}

    lower = text.lower()
    for label, keywords in KEYWORD_RULES:
        if label in labels and any(kw in lower for kw in keywords):
            return {"label": label, "confidence": 0.6, "source": "rules"}
    fallback = Intent.SEARCH.value if Intent.SEARCH.value in labels else labels[0]
    return {"label": fallback, "confidence": 0.3, "source": "rules"}
