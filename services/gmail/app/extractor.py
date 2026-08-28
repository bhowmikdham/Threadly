"""Tier-1 extraction: regexes catch 90%+ of order numbers, tracking codes,
amounts and dated commitments at sync time, so chat-time lookups are pure SQL.
Tier-2 (LLM residue) is a hook for later — rows it would add use the same
UNIQUE constraints, so both tiers dedupe against each other for free."""
import datetime as dt
import json
import re

ORDER_RE = re.compile(
    r"\b(?:order|confirmation|reference|booking)\s*(?:number|no\.?|num|#|id)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,24})\b",
    re.I,
)
TRACKING_RE = re.compile(
    r"\btracking\s*(?:number|no\.?|num|#|id)?\s*[:#]?\s*([A-Z0-9]{8,30})\b",
    re.I,
)
AMOUNT_RE = re.compile(
    r"\b(?:total|amount|charged|paid)\b\D{0,12}([$€£]\s?\d[\d,]*(?:\.\d{2})?)",
    re.I,
)

STOPWORD_KEYS = {"number", "confirmation", "details", "status", "update", "history"}

# A sentence containing a deadline phrase is a commitment candidate.
COMMITMENT_RE = re.compile(
    r"([^.!?\n]*\b(?:by|before|due)\s+"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|end of (?:the )?(?:day|week))"
    r"\b[^.!?\n]*)",
    re.I,
)
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def parse_due(phrase: str, base: dt.datetime) -> dt.datetime | None:
    """'by thursday' relative to when the email was sent -> a concrete
    end-of-day UTC timestamp. Same-day names mean today, not next week."""
    phrase = phrase.lower()
    eod = base.replace(hour=23, minute=59, second=0, microsecond=0)
    if phrase == "today" or phrase.startswith("end of") and "day" in phrase:
        return eod
    if phrase == "tomorrow":
        return eod + dt.timedelta(days=1)
    if phrase.startswith("end of") and "week" in phrase:
        return eod + dt.timedelta(days=(4 - base.weekday()) % 7)
    if phrase in WEEKDAYS:
        return eod + dt.timedelta(days=(WEEKDAYS[phrase] - base.weekday()) % 7)
    return None


def extract_commitments(message: dict) -> list[dict]:
    """message: {gmail_msg_id, sent_at, is_sent, body_text}. is_sent decides
    direction: the user's own mail carries promises, inbound mail carries asks."""
    text = message.get("body_text") or ""
    out, seen = [], set()
    for m in COMMITMENT_RE.finditer(text):
        description = " ".join(m.group(1).split()).strip(" -–—")[:200]
        if not description or description.lower() in seen:
            continue
        seen.add(description.lower())
        out.append({
            "description": description,
            "direction": "outbound" if message.get("is_sent") else "inbound",
            "due_at": parse_due(m.group(2), message["sent_at"]),
            "source_msg_id": message["gmail_msg_id"],
        })
    return out


def merchant_from_addr(from_addr: str | None) -> str | None:
    """orders@gyg.com.au -> GYG. Best-effort; entity rows keep the raw sender too."""
    if not from_addr or "@" not in from_addr:
        return None
    domain = from_addr.rsplit("@", 1)[1].lower()
    label = domain.split(".")[0]
    return label.upper() if label else None


def extract(message: dict) -> list[dict]:
    """message: {gmail_msg_id, from_addr, sent_at, subject?, body_text}.
    Returns entity dicts ready for upsert."""
    text = f"{message.get('subject', '')}\n{message.get('body_text', '')}"
    merchant = merchant_from_addr(message.get("from_addr"))
    entities: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(entity_type: str, key: str, value: dict):
        key = key.strip()
        if not key or key.lower() in STOPWORD_KEYS:
            return
        if entity_type in ("order", "tracking") and not any(c.isdigit() for c in key):
            return  # "order CONFIRMED" is not an order number
        if (entity_type, key) in seen:
            return
        seen.add((entity_type, key))
        entities.append({
            "type": entity_type,
            "key": key,
            "value": value,
            "merchant": merchant,
            "source_msg_id": message["gmail_msg_id"],
            "occurred_at": message["sent_at"],
        })

    for m in ORDER_RE.finditer(text):
        add("order", m.group(1), {"from": message.get("from_addr")})
    for m in TRACKING_RE.finditer(text):
        add("tracking", m.group(1), {"from": message.get("from_addr")})
    for m in AMOUNT_RE.finditer(text):
        add("amount", m.group(1).replace(" ", ""), {"from": message.get("from_addr")})
    return entities


# ------------------------------------------------------------ tier 2 (LLM)
# Only the residue goes to the LLM: messages that look transactional but
# where the regexes found nothing.

TRANSACTIONAL_HINT_RE = re.compile(
    r"\b(order|booking|confirmation|receipt|invoice|tracking|reservation|itinerary|shipped|delivery|refund)\b",
    re.I,
)
VALID_TIER2_TYPES = {"order", "tracking", "amount"}


def needs_tier2(message: dict, tier1_entities: list[dict]) -> bool:
    if tier1_entities:
        return False
    text = f"{message.get('subject', '')}\n{message.get('body_text', '')}"
    return bool(TRANSACTIONAL_HINT_RE.search(text))


def tier2_prompt(message: dict) -> str:
    text = f"Subject: {message.get('subject', '')}\n{message.get('body_text', '')}"[:1500]
    return (
        "Extract reference identifiers from this email. Allowed types: order, tracking, amount.\n"
        'Respond with ONLY a JSON array, e.g. [{"type": "order", "key": "AB-1234"}]. '
        "Use [] if there is nothing to extract. Never invent values.\n\n" + text
    )


def parse_tier2_response(raw: str, message: dict) -> list[dict]:
    """Defensive parse of the LLM's reply; items pass the same validity rules
    as tier 1 so a hallucinating model can't pollute the entity store."""
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []

    merchant = merchant_from_addr(message.get("from_addr"))
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        entity_type = str(item.get("type", "")).strip().lower()
        key = str(item.get("key", "")).strip()
        if entity_type not in VALID_TIER2_TYPES or not key or len(key) > 64:
            continue
        if key.lower() in STOPWORD_KEYS:
            continue
        if entity_type in ("order", "tracking") and not any(c.isdigit() for c in key):
            continue
        if (entity_type, key) in seen:
            continue
        seen.add((entity_type, key))
        out.append({
            "type": entity_type,
            "key": key,
            "value": {"from": message.get("from_addr"), "tier": 2},
            "merchant": merchant,
            "source_msg_id": message["gmail_msg_id"],
            "occurred_at": message["sent_at"],
        })
    return out
