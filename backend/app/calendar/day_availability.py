"""Bounded, deterministic whole-day availability questions over selected calendars."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.calendar import service
from app.calendar.availability import merge
from app.calendar.time_resolution import day_start
from app.schemas.calendar import FreeBusyRequest

POLICY = "calendar-day-answer-1.1.0"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DAY = r"(?:monday|tuesday|wednesday|thursday|thurday|friday|saturday|sunday)"
DATE = rf"(?:today|tomorrow|\d{{4}}-\d{{2}}-\d{{2}}|(?:this\s+)?{DAY}(?:\s+this\s+week)?)"
# Fast path for common wording; semantic paraphrases use the model's read tool.
QUESTION = re.compile(
    r"(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
    r"(?:(?:(?:tell\s+me|check|see)\s+(?:if|whether)\s+)?"
    r"(?:am\s+i|i\s+am|i['’]m|will\s+i\s+be)\s+(?:free|available|busy)|"
    r"(?:check|show|find)\s+(?:me\s+)?my\s+(?:availability|availabiloty))\s+"
    rf"(?:(?:on|for)\s+)?(?:the\s+)?(?P<date>{DATE})[?.! ]*",
    re.I,
)
CONFIRMATION = re.compile(r"(?:yes|yep|yeah|sure|ok(?:ay)?|please do|go ahead)[.! ]*", re.I)


def requested_day(instruction):
    """Recognize only standalone self-availability, never slot requests or compound actions.

    Follow-ups must be a bare confirmation. Assistant-authored dates are not inputs.
    More complex changes continue through the existing semantic coordinator.
    """
    parts = instruction.strip().split("\nUser follow-up: ")
    if any(not CONFIRMATION.fullmatch(part.strip()) for part in parts[1:]):
        return None
    match = QUESTION.fullmatch(" ".join(parts[0].split()))
    return match["date"].casefold().replace("thurday", "thursday") if match else None


def tool_day(instruction):
    """Extract dates only from user text; the semantic model supplies no date or IDs.

    This tool is a whole-day self read, never a subset of compound work. Reject
    windows/qualifiers we cannot preserve rather than silently widening the query.
    """
    parts = instruction.strip().split("\nUser follow-up: ")
    if any(not CONFIRMATION.fullmatch(part.strip()) for part in parts[1:]):
        raise ValueError("Ask for a standalone day-availability request")
    text = " ".join(parts[0].casefold().split())
    if not re.search(r"\b(?:i|my|me)\b", text) or re.search(
        r"\b(?:send|draft|write|reply|book|invite|schedule|reschedule|slots?|"
        r"someone|their|his|her|our|manager|colleague|team|cancel|stop|never|not)\b|don['’]t",
        text,
    ):
        raise ValueError("Use the complete reviewed workflow for compound or other-person work")
    matches = list(re.finditer(rf"\b{DATE}\b", text, re.I))
    if len(matches) != 1:
        raise ValueError("Ask which single day to check")
    match = matches[0]
    remainder = text[: match.start()] + text[match.end() :]
    if re.search(
        r"\b(?:next|last|following|previous|week|weekend|month|morning|afternoon|"
        r"evening|tonight|noon|midnight|before|after|between|until|from|lunch|sunrise|sunset|utc|gmt|"
        r"timezone|hours?|minutes?)\b|\d|/",
        remainder,
    ):
        raise ValueError("Ask about the requested date or time window instead of dropping it")
    return match[0].replace("thurday", "thursday")


def resolve_day(phrase, anchor, timezone):
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("A timezone-aware anchor is required")
    today = anchor.astimezone(ZoneInfo(timezone)).date()
    if phrase in {"today", "tomorrow"}:
        return today + timedelta(days=phrase == "tomorrow")
    weekday = next((day for day in WEEKDAYS if re.search(rf"\b{day}\b", phrase)), None)
    if weekday:
        offset = WEEKDAYS.index(weekday) - today.weekday()
        if "this" not in phrase:
            offset %= 7
        return today + timedelta(days=offset)
    return date.fromisoformat(phrase)


def render(evidence, timezone, day, *, remaining_day):
    label = day.strftime("%A, %-d %B %Y")
    scope = f"the rest of {label}" if remaining_day else label
    unavailable = [calendar for calendar in evidence.calendars if calendar.status != "known"]
    complete = evidence.coverage == "complete" and not unavailable and bool(evidence.calendars)
    busy = merge(
        (max(interval.start, evidence.start), min(interval.end, evidence.end))
        for calendar in evidence.calendars
        if calendar.status == "known"
        for interval in calendar.busy
    )
    warning = ""
    if not complete:
        names = [" ".join(c.display_name.split())[:80] for c in unavailable if c.display_name]
        subject = ", ".join(names) if names else "some selected calendars"
        warning = (
            f"I couldn't confirm your full availability on {label}: {subject} couldn't be checked. "
            "Open Review setup to check those calendar selections, or try again. "
            "Unchecked calendars may contain additional busy time."
        )
    prefix = f"On your selected calendars, {scope} ({timezone})"
    if not busy:
        return f"{prefix} has no busy time recorded." if complete else warning
    zone = ZoneInfo(timezone)

    def clock(instant):
        # Show the following day's midnight explicitly instead of an ambiguous 12 AM.
        local = instant.astimezone(zone)
        suffix = " next day" if local.date() > day else ""
        return local.strftime("%-I:%M %p") + suffix

    intervals = "; ".join(f"{clock(start)}–{clock(end)}" for start, end in busy[:10])
    suffix = f" Showing 10 of {len(busy)} busy periods." if len(busy) > 10 else ""
    return f"{prefix} has busy time: {intervals}.{suffix}" + (f"\n\n{warning}" if warning else "")


async def read(user_id, phrase, *, anchor=None):
    """Use existing owner, grant, ACL, freshness and preference-version fences."""
    pref = await service.get_preferences(user_id)
    timezone = pref.preferences.timezone
    anchor = anchor or datetime.now(UTC)
    try:
        day = resolve_day(phrase, anchor, timezone)
        zone = ZoneInfo(timezone)
        start = day_start(day, zone)
        end = day_start(day + timedelta(days=1), zone)
    except (ValueError, OverflowError):
        return {"kind": "clarification", "text": "Which date should I check? Use YYYY-MM-DD."}
    if end <= anchor:
        return {
            "kind": "clarification",
            "text": (
                f"{day.isoformat()} has already passed in {timezone}. "
                "Which future date should I check?"
            ),
        }
    # Today's completed hours cannot establish current availability. Query the remainder.
    remaining_day = start < anchor
    evidence = await service.query_freebusy(
        user_id,
        FreeBusyRequest(
            expected_preferences_version=pref.version, start=max(start, anchor), end=end
        ),
    )
    if (
        evidence.preferences_version != pref.version
        or evidence.account_version != pref.account_version
    ):
        raise service.conflict()
    return {
        "kind": "message",
        "text": render(evidence, timezone, day, remaining_day=remaining_day),
        "calendar_availability": {
            "date": day.isoformat(),
            "timezone": timezone,
            "start": evidence.start.isoformat(),
            "end": evidence.end.isoformat(),
            "coverage": evidence.coverage,
            "checked_at": evidence.checked_at.isoformat(),
            "expires_at": evidence.expires_at.isoformat(),
            "evidence_id": evidence.id,
        },
    }


async def answer(user_id, instruction, *, semantic=False):
    phrase = tool_day(instruction) if semantic else requested_day(instruction)
    if phrase is None:
        return None
    try:
        return await read(user_id, phrase)
    except ApiError as exc:
        messages = {
            "calendar_preferences_missing": (
                "Choose your calendars and timezone in Calendar settings, then ask again."
            ),
            "calendar_connection_required": (
                "Connect Calendar read access in settings, then ask again."
            ),
            "calendar_access_denied": (
                "Reconnect Calendar in settings, then check your selected calendars."
            ),
            "calendar_context_changed": (
                "Your Calendar settings changed during this check. Please try again."
            ),
            "calendar_window_invalid": "Choose a future date within the next 90 days.",
        }
        return {
            "kind": "message",
            "text": "I couldn't check your availability. "
            + messages.get(exc.code, "The Calendar check failed; please try again."),
            "error_code": exc.code,
        }
