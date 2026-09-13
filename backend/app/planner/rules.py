"""Tier-1 intent rules (module 4): cheap, deterministic, golden-tested.

Each rule is (compiled regex, Intent). First match wins. Keep patterns
aggressively simple — anything ambiguous falls through to the 2b model.
"""
import re

from app.orchestrator.intents import Intent

RULES: list[tuple[re.Pattern, Intent]] = [
    (re.compile(r"\b(summar(y|ise|ize)|catch me up|tl;?dr)\b", re.I), Intent.SUMMARISE),
    (re.compile(r"\b(draft|reply|respond|write back)\b", re.I), Intent.DRAFT),
    (
        re.compile(
            r"\b(what('| i)?s|find|show|when|where)\b"
            r".*\b(flight|booking|invoice|tracking|code|address|deadline)\b",
            re.I,
        ),
        Intent.FETCH_ENTITY,
    ),
    (
        re.compile(r"\b(owe|promised|commitments?|follow.?ups?|action items?)\b", re.I),
        Intent.FETCH_COMMITMENTS,
    ),
]


def match(utterance: str) -> Intent | None:
    for pattern, intent in RULES:
        if pattern.search(utterance):
            return intent
    return None
