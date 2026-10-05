"""Bounded, deterministic whole-day availability questions over selected calendars."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.calendar import service
from app.calendar.availability import merge
from app.calendar.time_resolution import day_start
from app.schemas.calendar import FreeBusyRequest

POLICY = "calendar-day-answer-2.0.0"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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
            f"I couldn't confirm your full availability on {label}. "
            f"{subject} couldn't be checked. Review calendars to fix this; "
            "they may contain additional busy time."
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


async def read(user_id, phrase, *, anchor=None, window=None, instruction=""):
    """Use existing owner, grant, ACL, freshness and preference-version fences."""
    pref = await service.get_preferences(user_id)
    timezone = pref.preferences.timezone
    anchor = anchor or datetime.now(UTC)
    try:
        if window is not None:
            from app.calendar.conversation_tools import resolve_window

            start, end = resolve_window(window, instruction, anchor, timezone)
            day = start.astimezone(ZoneInfo(timezone)).date()
            if end.astimezone(ZoneInfo(timezone)).date() != day + timedelta(days=1):
                raise ValueError("Whole-day availability requires exactly one day")
        else:
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
    result = {
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
    if evidence.coverage != "complete" or any(c.status != "known" for c in evidence.calendars):
        result["error_code"] = "calendar_coverage_incomplete"
    return result


async def answer(user_id, instruction, *, window, anchor=None):
    if window.subject == "other":
        return {
            "kind": "message",
            "text": (
                "I can check your selected calendars, but I don't have access "
                "to that person's availability."
            ),
        }
    phrase = window.date_phrase
    try:
        # A concurrent settings update can be recovered by starting a fresh read.
        # Re-resolve the entire day in the new timezone; never reuse old evidence.
        # Persistent stale preferences are a different error and are never retried.
        for attempt in range(2):
            try:
                return await read(
                    user_id, phrase, window=window, instruction=instruction, anchor=anchor
                )
            except ApiError as exc:
                if exc.code != "calendar_context_changed" or attempt:
                    raise
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
                "Your Calendar settings changed again. Review calendars, then try again."
            ),
            "calendar_preferences_stale": (
                "Your Calendar connection changed. Review and save your calendars, then ask again."
            ),
            "calendar_window_invalid": "Choose a future date within the next 90 days.",
        }
        return {
            "kind": "message",
            "text": "I couldn't check your availability. "
            + messages.get(exc.code, "The Calendar check failed; please try again."),
            "error_code": exc.code,
        }
