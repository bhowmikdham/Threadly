"""Strict JSON helpers shared by bounded model-output validators."""

import json
import re


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def json_object(text, *, max_chars):
    """One JSON object, optionally wrapped in one complete JSON Markdown fence.

    Never extracts a substring from prose, repairs JSON, drops fields or retries.
    Schema/ownership/action validation remains the caller's responsibility.
    """
    if not isinstance(text, str) or len(text) > max_chars:
        raise ValueError("JSON response exceeds bounds")
    value = text.strip()
    if value.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", value)
        if match is None:
            raise ValueError("Invalid JSON response envelope")
        value = match.group(1)

    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")

    result = json.loads(
        value, object_pairs_hook=reject_duplicate_keys, parse_constant=invalid_constant
    )
    if not isinstance(result, dict):
        raise ValueError("Expected one JSON object")
    return result
