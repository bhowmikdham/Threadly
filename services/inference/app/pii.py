"""PII masking for cloud egress. Prompts sent to OpenRouter get emails and
phone numbers replaced with stable placeholders; local (Ollama over Tailscale)
traffic is sent unmasked."""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Returns (masked_text, mapping placeholder -> original)."""
    mapping: dict[str, str] = {}
    counters = {"email": 0, "phone": 0}

    def _sub(kind: str, match: re.Match) -> str:
        original = match.group(0)
        for placeholder, known in mapping.items():
            if known == original:
                return placeholder
        counters[kind] += 1
        placeholder = f"<{kind}_{counters[kind]}>"
        mapping[placeholder] = original
        return placeholder

    text = EMAIL_RE.sub(lambda m: _sub("email", m), text)
    text = PHONE_RE.sub(lambda m: _sub("phone", m), text)
    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text
