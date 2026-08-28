"""Dev/fallback heuristics for the per-message classifiers (priority, action,
category). These are placeholders with the same output shape as the AI team's
BERTs — real models mounted under models/classifiers/<task>/ replace them.
Pure functions — unit-tested."""
import re

# Action label set agreed with the AI team (multiclass, exactly one per email).
ACTION_LABELS = ["approve", "review", "edit", "complete_submit", "attend", "reply", "no_action"]

_PRIORITY_HIGH_RE = re.compile(
    r"\burgent\b|\basap\b|\beod\b|\bdeadline\b|\boverdue\b|\bimportant\b|"
    r"\b(?:by|before|due)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|end of)\b",
    re.I,
)

_ACTION_RULES: list[tuple[str, re.Pattern]] = [
    ("approve", re.compile(r"\bapprov(e|al)\b|\bsign[- ]?off\b", re.I)),
    ("review", re.compile(r"\breview\b|\bfeedback\b|\btake a look\b|\bthoughts on\b", re.I)),
    ("complete_submit", re.compile(r"\bsubmit\b|\bfill (in|out)\b|\bcomplete the\b|\bform\b", re.I)),
    ("attend", re.compile(r"\bmeeting\b|\binvite\b|\bcalendar\b|\brsvp\b|\bjoin us\b", re.I)),
    ("reply", re.compile(r"\bcan you\b|\bcould you\b|\blet me know\b|\bplease (send|confirm|advise)\b|\?", re.I)),
]

_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("purchases", re.compile(r"\border\b|\breceipt\b|\binvoice\b|\bpayment\b|\btracking\b|\bshipped\b|\bbooking\b|\breservation\b", re.I)),
    ("scheduling", re.compile(r"\bmeeting\b|\bcalendar\b|\binvite\b|\breschedule\b|\bappointment\b", re.I)),
    ("newsletters", re.compile(r"\bunsubscribe\b|\bnewsletter\b|\bdigest\b|\bweekly update\b", re.I)),
    ("work", re.compile(r"\breport\b|\bproject\b|\bdeadline\b|\bq[1-4]\b|\bboard\b", re.I)),
]


def rule_priority(text: str) -> dict:
    if _PRIORITY_HIGH_RE.search(text):
        return {"label": "high", "confidence": 0.5, "source": "rules"}
    return {"label": "normal", "confidence": 0.4, "source": "rules"}


def rule_action(text: str) -> dict:
    for label, pattern in _ACTION_RULES:
        if pattern.search(text):
            return {"label": label, "confidence": 0.5, "source": "rules"}
    return {"label": "no_action", "confidence": 0.4, "source": "rules"}


def rule_category(text: str) -> dict:
    for label, pattern in _CATEGORY_RULES:
        if pattern.search(text):
            return {"label": label, "confidence": 0.5, "source": "rules"}
    return {"label": "other", "confidence": 0.3, "source": "rules"}


RULE_CLASSIFIERS = {"priority": rule_priority, "action": rule_action, "category": rule_category}
