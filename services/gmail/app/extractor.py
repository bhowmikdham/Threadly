"""Tier-1 extraction: regexes catch 90%+ of order numbers, tracking codes and
amounts at sync time, so chat-time lookups are pure SQL. Tier-2 (LLM residue)
is a hook for later — rows it would add use the same UNIQUE(user,type,key)
constraint, so both tiers dedupe against each other for free."""
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
