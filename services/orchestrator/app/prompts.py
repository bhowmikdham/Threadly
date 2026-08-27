"""Prompt templates. The AI team owns the wording via models/prompts.yaml;
the backend owns the slot names. Slots are the contract — see CONTRACTS.md."""
import logging
import os

import yaml

from . import config

log = logging.getLogger(__name__)

DEFAULTS: dict[str, str] = {
    "system": (
        "You are Threadly, an email assistant living in the user's Gmail side panel. "
        "Be concise and factual. Never invent order numbers, dates, or names."
    ),
    "draft_email": (
        "Write an email on the user's behalf.\n"
        "User request: {instruction}\n\n"
        "Conversation so far (may be empty):\n{thread_context}\n\n"
        "Examples of the user's own writing style (may be empty):\n{rag_examples}\n\n"
        "Reply with the email body only — no subject line, no commentary."
    ),
    "summarise_thread": (
        "Summarise this email thread in 3-5 short bullet points, most recent "
        "development first. Include concrete dates, amounts and commitments.\n\n"
        "{thread_text}"
    ),
    "search_answer": (
        "Answer the user's question using ONLY the email excerpts below. "
        "If the excerpts don't contain the answer, say so.\n\n"
        "Question: {query}\n\nExcerpts:\n{snippets}"
    ),
}

_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        templates = dict(DEFAULTS)
        if os.path.exists(config.PROMPTS_PATH):
            try:
                with open(config.PROMPTS_PATH) as f:
                    overrides = yaml.safe_load(f) or {}
                templates.update({k: str(v) for k, v in overrides.items()})
                log.info("loaded prompt overrides from %s: %s", config.PROMPTS_PATH, sorted(overrides))
            except Exception:
                log.exception("failed to load %s; using defaults", config.PROMPTS_PATH)
        _cache = templates
    return _cache


def render(name: str, **slots: str) -> str:
    return _load()[name].format(**slots)


def system_prompt() -> str:
    return _load()["system"]
