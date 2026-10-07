"""User provenance for model-selected Calendar operations, never a positive intent parser."""

import re

from app.calendar.event_draft import IntentSourceMismatch

POLICY = "calendar-semantic-intent-1.0.0"


class IntentRequired(ValueError):
    """The model must supply the typed operation and its current USER source."""


class IntentNotAuthorized(ValueError):
    """Quoted, reported, negated or content-only text cannot authorize creation."""


def user_directive(text):
    # Pasted source sections are not part of the current top-level instruction.
    return re.split(r"\n|(?<!\d):(?!\d)", text.strip(), maxsplit=1)[0]


def validate_source(intent, text):
    if intent is None:
        raise IntentRequired
    if " ".join(intent.source.split()) != " ".join(user_directive(text).split()):
        raise IntentSourceMismatch


def validate_creation(text, title=""):
    """Check disqualifying source boundaries; the model supplies positive intent.

    There is deliberately no creation-verb, prefix, word-order or title/time grammar.
    These conservative exclusions prevent source/content instructions from being
    promoted by an erroneous tool call, including in an already-enabled Always mode.
    """
    directive = user_directive(text)
    # An explicitly named event may legitimately be called "Draft" or "Don't forget".
    # A model-selected arbitrary title must not hide a negation or a content request.
    if title:
        directive = re.sub(
            r"\b(?:called|named|titled)\s+[\"“']?" + re.escape(title),
            "named event",
            directive,
            flags=re.I,
        )
    unquoted = re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`|(?<!\w)\'[^\'\n]*\'(?!\w)', " ", directive)
    if not unquoted.strip(" .!?'\"“”`<>"):
        raise IntentNotAuthorized
    if unquoted != directive and re.search(
        r"\b(?:email|message|text|instruction)\b", unquoted, re.I
    ):
        raise IntentNotAuthorized
    if re.search(
        r"\b(?:don't|don’t|do\s+not|never|must\s+not|should\s+not|shouldn't|shouldn’t|"
        r"not\s+yet|no\s+need\s+to|what\s+if|hypothetically|imagine|suppose)\b",
        unquoted,
        re.I,
    ):
        raise IntentNotAuthorized
    # These are source/reporting boundaries, not lists of accepted request prefixes.
    if re.search(
        r"\b(?:summari[sz]e|explain|translate|quote|rewrite)\b|"
        r"\b(?:show\s+me\s+how|how\s+(?:do|would|can)\s+(?:I|you)|"
        r"what\s+does\s+(?:this|the)\s+instruction\s+mean|"
        r"what(?:'s|\s+is)\s+on\s+my\s+(?:schedule|calendar)|am\s+I\s+free)\b|"
        r"\b(?:email|message|text|instruction|sender|he|she|they|someone)\s+"
        r"(?:says?|said|reads?|asked|instructs?|told)\b|"
        r"\b(?:read|draft|reply\s+to)\s+(?:(?:the|this|a|an)\s+)?(?:email|message|reply)\b",
        unquoted,
        re.I,
    ):
        raise IntentNotAuthorized
    # A content-generation request is not Calendar creation. Explicitly named
    # event titles were removed above; ordinary meeting titles need no whitelist.
    if re.search(
        r"\b(?:create|make|write|put\s+together)\s+"
        r"(?:(?:a|an|the|short|concise|brief|new)\s+)*"
        r"(?:summary|summaries|draft|reply|email|message|explanation|translation|"
        r"instructions?|sentence|paragraph|essay|document|description|response|story|poem|code|script)\b",
        unquoted,
        re.I,
    ):
        raise IntentNotAuthorized
    if re.search(r"\bfor\s+(?:a\s+)?(?:summary|draft|reply|explanation)\s*[.!?]*$", unquoted, re.I):
        raise IntentNotAuthorized
