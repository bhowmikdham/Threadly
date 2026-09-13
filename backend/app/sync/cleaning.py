"""Body cleaning (module 3): strip quoted replies, signatures, and cruft
BEFORE anything is persisted — messages.body_clean is the only body we store.

Deterministic => golden-tested (tests/goldens). Extend patterns, never weaken
silently.
"""
import re

_QUOTE_HEADER = re.compile(
    r"^On .{0,120}(wrote|écrit)\s*:\s*$"          # "On Mon, 3 Aug 2026 ... wrote:"
    r"|^-{2,}\s*Original Message\s*-{2,}$"
    r"|^From:\s.+$"                                # forwarded-block header start
    r"|^_{5,}\s*$",
    re.I | re.M,
)
_SIG_MARKER = re.compile(r"^--\s*$|^Sent from my \w+|^Get Outlook for \w+", re.I | re.M)
_URL_TRACKER = re.compile(r"\[image:[^\]]*\]|​|­")


def clean_body(raw_text: str) -> str:
    """Cut at the first quoted-reply header, drop the signature block, squeeze whitespace."""
    text = _URL_TRACKER.sub("", raw_text or "")

    m = _QUOTE_HEADER.search(text)
    if m:
        text = text[: m.start()]

    # drop everything under a signature marker, and dangling quote lines
    m = _SIG_MARKER.search(text)
    if m:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]

    out = "\n".join(lines)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
