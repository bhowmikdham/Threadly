"""Current user provenance for model-interpreted, review-only workflow preparation."""

import re


def validate(source, current):
    if source != current:
        raise ValueError("Copy the complete current USER turn as request_source")
    directive = current.strip().splitlines()[0]
    # Reject obvious reported/quoted/negated directives. Positive interpretation
    # belongs to the model, not an ever-growing creation-verb whitelist. This
    # permits preparation only; execution keeps its separate approval boundary.
    unquoted = re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', "", directive).strip()
    if not unquoted.strip(" .!?:") or re.match(
        r"(?:don't|don’t|do not|never|stop|cancel|what if|hypothetically|"
        r"(?:the |this )?(?:email|message|sender|instruction) (?:says|said|asked))\b",
        unquoted,
        re.I,
    ):
        raise ValueError("Use the current user request, not a quoted or declined operation")
