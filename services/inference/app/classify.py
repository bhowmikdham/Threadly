"""Classification service, multi-task to match the AI team's actual models:

- task="intent"   -> chat planner fallback: BERT -> small-model JSON -> rules
- task="priority" |
- task="action"   |-> per-EMAIL classifiers applied at sync time:
- task="category" |   BERT (models/classifiers/<task>/) -> keyword rules

Same response shape everywhere: {label, confidence, source: bert|llm|rules}."""
import json
import logging
import os
import re

from threadly_common.models import Intent

from . import config
from .backends import router
from .label_rules import RULE_CLASSIFIERS

log = logging.getLogger(__name__)

INTENT_LABELS = [i.value for i in Intent if i != Intent.UNKNOWN]
MESSAGE_TASKS = tuple(RULE_CLASSIFIERS)  # priority, action, category

_pipelines: dict[str, object | None] = {}


def _model_dirs(task: str) -> list[str]:
    dirs = [os.path.join(config.CLASSIFIERS_DIR, task)]
    if task == "intent":
        dirs.append(config.BERT_DIR)  # legacy single-model location
    return dirs


def _load_pipeline(task: str):
    """HF pipeline for a task's mounted model, cached; None when absent."""
    if task in _pipelines:
        return _pipelines[task]
    _pipelines[task] = None
    model_dir = next((d for d in _model_dirs(task) if os.path.isdir(d)), None)
    if model_dir is None:
        log.info("no model dir for task %r; using fallbacks", task)
        return None
    try:
        from transformers import pipeline  # optional dep (requirements-bert.txt)
        _pipelines[task] = pipeline("text-classification", model=model_dir, top_k=1)
        log.info("loaded %r classifier from %s", task, model_dir)
    except ImportError:
        log.warning("%s exists but transformers is not installed; rebuild with WITH_BERT=1", model_dir)
    except Exception:
        log.exception("failed to load %r classifier from %s", task, model_dir)
    return _pipelines[task]


def loaded_classifiers() -> dict:
    """For /healthz: which tasks have a real model mounted right now."""
    out = {}
    for task in ("intent", *MESSAGE_TASKS):
        out[task] = "bert" if any(os.path.isdir(d) for d in _model_dirs(task)) else "fallback"
    return out


def _bert_classify(task: str, text: str, labels: list[str] | None) -> dict | None:
    bert = _load_pipeline(task)
    if bert is None:
        return None
    [result] = bert(text[:512])
    top = result[0] if isinstance(result, list) else result
    if labels and top["label"] not in labels:
        return None
    return {"label": top["label"], "confidence": float(top["score"]), "source": "bert"}


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
        match = re.search(r"\{.*\}", "".join(chunks), re.S)
        if not match:
            return None
        label = json.loads(match.group(0)).get("label", "").strip()
        if label in labels:
            return {"label": label, "confidence": 0.7, "source": "llm"}
    except Exception:
        log.exception("LLM classify fallback failed")
    return None


INTENT_KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (Intent.SUMMARISE.value, ["summar", "tldr", "tl;dr", "recap", "catch me up"]),
    (Intent.DRAFT.value, ["draft", "compose", "reply", "respond", "write back"]),
    (Intent.FETCH_ENTITY.value, ["order", "tracking", "invoice", "receipt", "refund", "booking", "commitment", "promised", "deadline"]),
]


async def classify(text: str, labels: list[str] | None = None, task: str = "intent") -> dict:
    if task in MESSAGE_TASKS:
        if verdict := _bert_classify(task, text, labels):
            return verdict
        return RULE_CLASSIFIERS[task](text)

    # intent (chat planner fallback)
    labels = labels or INTENT_LABELS
    if verdict := _bert_classify("intent", text, labels):
        return verdict
    if verdict := await _llm_classify(text, labels):
        return verdict
    lower = text.lower()
    for label, keywords in INTENT_KEYWORD_RULES:
        if label in labels and any(kw in lower for kw in keywords):
            return {"label": label, "confidence": 0.6, "source": "rules"}
    fallback = Intent.SEARCH.value if Intent.SEARCH.value in labels else labels[0]
    return {"label": fallback, "confidence": 0.3, "source": "rules"}
