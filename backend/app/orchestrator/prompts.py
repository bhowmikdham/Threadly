"""Prompt loader: /ml/prompts/prompts.yaml (AI-team owned) with built-in
fallbacks so the pipeline works before they ship. Version is logged so
ablation runs can pin exactly what produced an output."""
import logging
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import get_settings

log = logging.getLogger("threadly.prompts")

_FALLBACKS = {
    "summarise_thread": (
        "You are an email assistant. Summarise the email thread below for its owner.\n"
        "Cover: decisions made, action items (who owes what), open questions, deadlines.\n"
        "Only use information in the thread. If something is unclear, say so.\n"
        "Be concise: 3-6 bullet points.\n\nTHREAD:\n{thread_text}\n\nSUMMARY:"
    ),
}


@lru_cache
def _load() -> tuple[dict, str]:
    path = Path(get_settings().ml_dir) / "prompts" / "prompts.yaml"
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return data, str(data.get("version", "unversioned"))
    except FileNotFoundError:
        log.info("prompts.yaml not found at %s — using built-in fallbacks", path)
        return {}, "fallback"
    except Exception as exc:
        log.warning("prompts.yaml unreadable (%s) — using built-in fallbacks", exc)
        return {}, "fallback"


def get_prompt(name: str, **variables: str) -> str:
    data, _version = _load()
    entry = data.get(name) or {}
    template = entry.get("template") if isinstance(entry, dict) else None
    if not template or "TODO" in template:
        template = _FALLBACKS[name]
    return template.format(**variables)


def prompts_version() -> str:
    return _load()[1]
