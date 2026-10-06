"""Transient mail display values; never change source bodies or timestamp provenance."""

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

POLICY = "mail-presentation-1.0"
_INVISIBLE = re.compile(r"[\u00ad\u200b\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
_JOINERS = re.compile(r"[\u200c\u200d]+")
_URL = re.compile(r"https?://[^\s<>]+", re.I)
_FOOTER = re.compile(
    r"^\s*(?:unsubscribe\b|manage (?:your )?(?:email |notification )?preferences\b|"
    r"view (?:this (?:email|message) )?in (?:your )?browser\b|"
    r"privacy policy\b|you (?:are receiving|received) this (?:email|message) because\b)",
    re.I,
)


def snippet(value, limit=220):
    # Decode email preheader entities before truncation, including &amp;zwnj;.
    text = (value or "")[:24000]
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _INVISIBLE.sub("", text)
    # Repeated/boundary joiners are padding. Preserve meaningful joiners within
    # words and emoji sequences instead of deleting every Unicode format character.
    text = _JOINERS.sub(
        lambda m: (
            m[0]
            if len(m[0]) == 1
            and m.start() > 0
            and m.end() < len(text)
            and not text[m.start() - 1].isspace()
            and not text[m.end()].isspace()
            else ""
        ),
        text,
    )
    lines = []
    for line in text.splitlines():
        if _FOOTER.match(line):
            break
        lines.append(line)
    text = " ".join(lines)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^\s)]+\)", r"\1", text)
    text = _URL.sub("", text)
    text = re.sub(r"\\([\\`*_{}\[\]()>#+.!-])", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = " ".join(text.split()).strip()
    return text[:limit].rstrip()


def received_display(received_at, timezone):
    """Render the provider instant in the request zone, including its DST offset."""
    if not received_at:
        return {}
    received = datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        return {}
    local = received.astimezone(ZoneInfo(timezone))
    return {
        "received_at_local": local.isoformat(),
        "received_timezone": timezone,
        "received_at_display": (
            f"{local.strftime('%d %b %Y, %I:%M %p %Z')} ({timezone}, UTC{local.strftime('%z')})"
        ),
        "timestamp_source": "gmail.internalDate",
    }
