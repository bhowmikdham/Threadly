"""Planner: rules first, classifier fallback (decided by the caller when we
return UNKNOWN). Pure functions — no I/O — so the rules are unit-testable."""
import re

from threadly_common.models import Intent, Plan

SUMMARISE_RE = re.compile(r"\b(summari[sz]e|summary|tl;?dr|catch me up|recap)\b", re.I)

# Drafting is imperative: "draft/compose" anywhere, or reply/respond/write at
# the start. "what did he write?" must NOT land here.
DRAFT_STRONG_RE = re.compile(r"\b(draft|compose)\b", re.I)
DRAFT_START_RE = re.compile(r"^\s*(reply|respond|write|answer)\b", re.I)

ENTITY_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("order", re.compile(r"\border(s)?\b|\bpurchase(s)?\b", re.I)),
    ("tracking", re.compile(r"\btracking\b|\bshipment\b|\bwhere('s| is) my (package|parcel|delivery|order)\b", re.I)),
    ("amount", re.compile(r"\breceipt(s)?\b|\binvoice(s)?\b|\bcharged?\b|\bhow much did i (pay|spend)\b", re.I)),
]

MERCHANT_RE = re.compile(r"\b(?:for|from|with|at)\s+([A-Z][A-Za-z0-9&._-]{1,29})")
WINDOW_RE = re.compile(r"\blast\s+(\d+)\s*(day|week|month)s?\b", re.I)
QUESTION_START_RE = re.compile(r"^\s*(who|what|when|where|which|why|how|show|find|search|list|do i|did i|have i|is there|are there)\b", re.I)


def parse_window_days(message: str) -> int | None:
    m = WINDOW_RE.search(message)
    if not m:
        return None
    return int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2).lower()]


def parse_merchant(message: str) -> str | None:
    m = MERCHANT_RE.search(message)
    return m.group(1).strip(".,!?") if m else None


def plan(message: str) -> Plan:
    text = message.strip()
    if not text:
        return Plan(intent=Intent.UNKNOWN, confidence=0.0)

    if SUMMARISE_RE.search(text):
        return Plan(intent=Intent.SUMMARISE)

    if DRAFT_STRONG_RE.search(text) or DRAFT_START_RE.search(text):
        return Plan(intent=Intent.DRAFT, params={"instruction": text})

    for entity_type, pattern in ENTITY_TYPE_PATTERNS:
        if pattern.search(text):
            params: dict = {"type": entity_type}
            if merchant := parse_merchant(text):
                params["merchant"] = merchant
            if window := parse_window_days(text):
                params["window_days"] = window
            return Plan(intent=Intent.FETCH_ENTITY, params=params)

    if QUESTION_START_RE.search(text) or "?" in text:
        return Plan(intent=Intent.SEARCH, params={"query": text})

    return Plan(intent=Intent.UNKNOWN, confidence=0.0)
