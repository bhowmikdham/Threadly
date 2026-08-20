"""Module 9 — PII MASKING (build: W3, hardened W4).

Applied to EVERY payload leaving the box for a cloud service (OpenRouter,
ElevenLabs). Local/tailscale inference sees unmasked text; cloud never does.

mask() must be deterministic and reversible per-request (placeholder map), so
model output referencing <EMAIL_1> can be re-hydrated before display.
Golden tests cover this module — it's a freeze gate (W4).
"""
import re

# v0 seed patterns — W3/W4 extends this list (addresses, calendar links, names via classifier)
_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "PHONE": re.compile(r"(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)?\d{3,4}[ .-]?\d{3,4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace PII with stable placeholders. Returns (masked_text, placeholder_map)."""
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
