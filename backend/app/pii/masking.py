"""Reversible masking of three direct identifier patterns before cloud egress.

This seed policy covers email addresses, phone-like strings and long card-like
numbers. It is not full de-identification: names, postal addresses, order IDs,
dates and message content may remain. Product/operator privacy controls must
treat a configured cloud model as a processor of the remaining content.
"""

import re

# Seed patterns. Keep documentation explicit when this list changes.
_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "PHONE": re.compile(r"(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)?\d{3,4}[ .-]?\d{3,4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace supported identifiers with stable per-request placeholders."""
    mapping: dict[str, str] = {}
    masked = text
    for label, pattern in _PATTERNS.items():
        counter = 0

        def _sub(m, label=label):
            nonlocal counter
            counter += 1
            key = f"<{label}_{counter}>"
            mapping[key] = m.group(0)
            return key

        masked = pattern.sub(_sub, masked)
    return masked, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text
