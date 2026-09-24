"""Reversible masking of three direct identifier patterns before cloud egress.

This seed policy covers email addresses, phone-like strings and long card-like
numbers. It is not full de-identification: names, postal addresses, order IDs,
dates and message content may remain. Product/operator privacy controls must
treat a configured cloud model as a processor of the remaining content.
"""

import json
import re
from collections import defaultdict
from typing import Any

# Seed patterns. Keep documentation explicit when this list changes.
_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "PHONE": re.compile(r"(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?)?\d{3,4}[ .-]?\d{3,4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}
_PLACEHOLDER = re.compile(r"<(?:EMAIL|CARD|PHONE)_\d+>")


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


def mask_structure(value: Any) -> tuple[Any, dict[str, str]]:
    """Mask decoded string values with one stable map, including JSON text leaves.

    Masking a serialized object can replace digits inside ``\\u`` escapes and make
    the transport JSON invalid. A conversation can also carry JSON *inside* a text
    leaf, so decode those object/array leaves before masking and re-encode them.
    User-data dictionary keys are model-visible too, so mask them as values.
    Bedrock protocol keys have no matching private pattern and remain unchanged;
    actual tool-use ID values are left byte-for-byte intact.
    """
    mapping: dict[str, str] = {}
    placeholders: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = defaultdict(int)

    def original_strings(item: Any):
        if isinstance(item, str):
            yield item
            if item.strip().startswith(("{", "[")):
                try:
                    nested_json = json.loads(item)
                except (ValueError, TypeError):
                    pass
                else:
                    if isinstance(nested_json, (dict, list)):
                        yield from original_strings(nested_json)
        elif isinstance(item, dict):
            for key, nested in item.items():
                yield from original_strings(key)
                yield from original_strings(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                yield from original_strings(nested)

    # A user may literally type a placeholder. Never allocate that token to a
    # different value, or unmasking a model answer could change the user's text.
    reserved = {
        token for original in original_strings(value) for token in _PLACEHOLDER.findall(original)
    }

    def placeholder(label: str, original: str) -> str:
        lookup = (label, original)
        if lookup not in placeholders:
            while True:
                counts[label] += 1
                key = f"<{label}_{counts[label]}>"
                if key not in reserved:
                    break
            placeholders[lookup] = key
            mapping[key] = original
        return placeholders[lookup]

    def mask_text(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                pass  # Malformed JSON remains ordinary text, never a hard failure.
            else:
                if isinstance(parsed, (dict, list)):
                    return json.dumps(walk(parsed, ("embedded",)), ensure_ascii=True)

        # Long card numbers can also satisfy the broad phone pattern. Consume
        # them first so the whole card is represented by one placeholder.
        for label in ("EMAIL", "CARD", "PHONE"):
            pattern = _PATTERNS[label]

            def replace(match: re.Match[str], label: str = label) -> str:
                return placeholder(label, match.group(0))

            text = pattern.sub(replace, text)
        return text

    def walk(item: Any, path: tuple[Any, ...]) -> Any:
        if isinstance(item, str):
            return mask_text(item)
        if isinstance(item, list):
            return [walk(nested, (*path, index)) for index, nested in enumerate(item)]
        if isinstance(item, dict):
            return {
                mask_text(key) if isinstance(key, str) else key: (
                    nested
                    if key == "toolUseId"
                    and len(path) == 5
                    and path[0] == "messages"
                    and isinstance(path[1], int)
                    and path[2] == "content"
                    and isinstance(path[3], int)
                    and path[4] in {"toolUse", "toolResult"}
                    else walk(nested, (*path, key))
                )
                for key, nested in item.items()
            }
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            literal = str(item)
            for label in ("CARD", "PHONE"):
                if _PATTERNS[label].search(literal):
                    # JSON numbers cannot contain unquoted placeholders. A
                    # string placeholder preserves valid nested JSON and avoids
                    # sending even a partially matched numeric identifier.
                    return placeholder(label, literal)
        return item

    return walk(value, ()), mapping
