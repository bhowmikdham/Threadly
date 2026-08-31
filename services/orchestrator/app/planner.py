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
    ("commitment", re.compile(r"\bcommitments?\b|\bpromised?\b|\bdeadlines?\b|\bwhat('s| is) due\b|\bfollow[- ]?ups?\b|\bowe\b", re.I)),
]

# Merchant phrases arrive two ways:
#   trigger form:    "orders FROM guzman y gomez" / "invoice FOR GIG"
#   possessive form: "my gyg orders" / "our amazon receipts"
MERCHANT_PHRASE_RE = re.compile(
    r"\b(?:for|from|with|at)\s+([A-Za-z0-9][\w&'.-]*(?:\s+[A-Za-z0-9][\w&'.-]*){0,3})", re.I
)
POSSESSIVE_MERCHANT_RE = re.compile(
    r"\b(?:my|our)\s+([A-Za-z0-9][\w&'. -]*?)\s+"
    r"(?:order|purchase|receipt|invoice|booking|charge|amount|tracking|email)s?\b",
    re.I,
)
MERCHANT_STOPWORDS = {
    # articles / fillers
    "the", "a", "an", "my", "me", "our", "all", "any", "this", "that", "these", "those",
    # time words
    "last", "past", "previous", "recent", "latest", "first", "ever", "so", "far",
    "week", "weeks", "month", "months", "year", "years", "day", "days",
    "today", "yesterday", "tomorrow", "ago", "now",
    # entity nouns and mail words
    "order", "orders", "purchase", "purchases", "receipt", "receipts",
    "invoice", "invoices", "email", "emails", "mail", "inbox", "gmail", "account",
    # verbs / connectors — including the trigger words themselves, so a second
    # "from"/"for" inside the captured phrase never becomes a merchant (E1)
    "i", "did", "do", "have", "had", "please", "again", "and", "in", "on", "of",
    "it", "them", "for", "from", "with", "at", "by", "about", "show", "get",
    "give", "find", "list",
}

# A pasted reference key ("order GYG-84640") filters the lookup to that key (E4).
KEY_TOKEN_RE = re.compile(
    r"\b([A-Za-z]{1,8}-[A-Za-z0-9][\w-]*\d[\w-]*"   # GYG-84640, INV-2043
    r"|\d{6,}"                                       # 8471023941
    r"|[A-Za-z]{1,5}\d{4,}[\w-]*)\b"                 # AB12345
)

# Bare "last month"/"past week" count as 1 unit (E7).
WINDOW_RE = re.compile(r"\b(?:last|past)\s+(?:(\d+)\s*)?(day|week|month|year)s?\b", re.I)
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

QUESTION_START_RE = re.compile(r"^\s*(who|what|when|where|which|why|how|show|find|search|list|do i|did i|have i|is there|are there)\b", re.I)


def parse_window_days(message: str) -> int | None:
    m = WINDOW_RE.search(message)
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else 1
    return count * UNIT_DAYS[m.group(2).lower()]


def _clean_phrase(phrase: str) -> str | None:
    words = []
    for word in phrase.split():
        word = word.strip(".,!?")
        if word and not word.isdigit() and word.lower() not in MERCHANT_STOPWORDS:
            words.append(word)
    return " ".join(words[:4]) if words else None


def parse_merchant(message: str) -> str | None:
    for pattern in (MERCHANT_PHRASE_RE, POSSESSIVE_MERCHANT_RE):
        for m in pattern.finditer(message):
            if cleaned := _clean_phrase(m.group(1)):
                return cleaned
    return None


def parse_key(message: str) -> str | None:
    for m in KEY_TOKEN_RE.finditer(message):
        token = m.group(1)
        if any(c.isdigit() for c in token):
            return token
    return None


def _entity_plan(text: str) -> Plan | None:
    for entity_type, pattern in ENTITY_TYPE_PATTERNS:
        if pattern.search(text):
            params: dict = {"type": entity_type}
            if merchant := parse_merchant(text):
                params["merchant"] = merchant
            if window := parse_window_days(text):
                params["window_days"] = window
            if entity_type != "commitment" and (key := parse_key(text)):
                params["key"] = key
            return Plan(intent=Intent.FETCH_ENTITY, params=params)
    return None


def plan(message: str, has_thread: bool = False) -> Plan:
    text = message.strip()
    if not text:
        return Plan(intent=Intent.UNKNOWN, confidence=0.0)

    entity_plan = _entity_plan(text)

    if SUMMARISE_RE.search(text):
        # "summarise my GYG orders" is a merchant lookup, not a thread summary —
        # unless the panel supplied a thread to summarise (E3).
        if not has_thread and entity_plan is not None and entity_plan.params.get("merchant"):
            return entity_plan
        return Plan(intent=Intent.SUMMARISE)

    if DRAFT_STRONG_RE.search(text) or DRAFT_START_RE.search(text):
        return Plan(intent=Intent.DRAFT, params={"instruction": text})

    if entity_plan is not None:
        return entity_plan

    if QUESTION_START_RE.search(text) or "?" in text:
        return Plan(intent=Intent.SEARCH, params={"query": text})

    return Plan(intent=Intent.UNKNOWN, confidence=0.0)
