"""Tier-1 extraction patterns (module 6). Deterministic, golden-tested.

Target: 90%+ of entity volume from regex alone. Each entry yields
(type, key, value) candidates; the LLM (tier 2) only ever sees messages where
tier 1 found nothing relevant.
"""
import re

# type -> compiled pattern (v0 seeds — extend with goldens, never weaken silently)
PATTERNS: dict[str, re.Pattern] = {
    "flight": re.compile(r"\b([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{2,4})\b"),
    "tracking_number": re.compile(r"\b(1Z[0-9A-Z]{16}|\d{12,22})\b"),
    "otp_code": re.compile(r"\b(\d{6})\b(?=.*\b(code|otp|verification)\b)", re.I),
    "amount": re.compile(r"(?:AUD|USD|\$|€|£)\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?"),
    "date_deadline": re.compile(
        r"\b(?:by|due|before|on)\s+((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})",
        re.I,
    ),
    "booking_ref": re.compile(
        r"\b(?:booking|confirmation|reference|ref)[\s#:]*([A-Z0-9]{5,8})\b", re.I
    ),
}
