"""Ingestion normalization — the single cleaning stage every downstream
consumer inherits (extractor, FTS search, classifiers, RAG, summaries).
Real transactional mail is frequently HTML-only; real threads carry forwarded
banners and signatures. Normalizing once at sync time also keeps runtime text
shaped like the AI team's cleaned training data (no train/serve skew).
Pure stdlib — unit-tested."""
import base64
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "ul", "ol", "section", "article",
    "header", "footer", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
}
_CELL_TAGS = {"td", "th"}
_SKIP_TAGS = {"script", "style", "head", "title", "meta", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in _CELL_TAGS:
            self.parts.append(" ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# Scraping/forwarding artifacts the AI team also strips from training data.
_FORWARD_BANNER_RE = re.compile(r"^\s*[=\-]{2,}.*forwarded (?:by|message).*$", re.I | re.M)
_ORIGINAL_MSG_RE = re.compile(r"^\s*-{2,}\s*original message\s*-{2,}\s*$", re.I | re.M)
_SIG_DELIMITER_RE = re.compile(r"^--\s*$")
_SIG_MAX_LINES = 15  # only treat "--" as a signature cut near the end


def clean_email_text(text: str) -> str:
    text = _FORWARD_BANNER_RE.sub("", text)
    text = _ORIGINAL_MSG_RE.sub("", text)

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SIG_DELIMITER_RE.match(line) and len(lines) - i <= _SIG_MAX_LINES:
            lines = lines[:i]
            break

    cleaned = "\n".join(line.rstrip() for line in lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _find_part(payload: dict, mime: str) -> str:
    """Depth-first hunt for a MIME part's decoded body in a Gmail payload."""
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode(errors="replace")
    for part in payload.get("parts") or []:
        if found := _find_part(part, mime):
            return found
    return ""


def extract_body(payload: dict) -> str:
    """text/plain when the sender provides it; otherwise the HTML part
    rendered to text. This is what makes HTML-only transactional mail
    (most order confirmations) visible to the extractor at all."""
    if plain := _find_part(payload, "text/plain"):
        return plain
    if html := _find_part(payload, "text/html"):
        return html_to_text(html)
    return ""
