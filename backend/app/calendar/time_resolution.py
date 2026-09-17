"""Resolve bounded typed dates/clocks against a SAVED anchor and visible context."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.calendar.availability import fits_working


def parse_clock(value, meridiem=None):
    match = re.fullmatch(r"(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?", value.strip().lower())
    if not match:
        raise ValueError("Use a clock time such as 4, 4 pm or 16:00.")
    hour, minute = int(match[1]), int(match[2] or 0)
    suffix = match[3].upper() if match[3] else None
    if suffix and meridiem and suffix != meridiem:
        raise ValueError("Clock time and meridiem conflict.")
    suffix = suffix or meridiem
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError("AM/PM clocks use hours 1 through 12.")
        return [(hour % 12 + (12 if suffix == "PM" else 0)) * 60 + minute]
    if not 0 <= hour <= 23:
        raise ValueError("Hour is outside the supported range.")
    # Two-digit HH:mm is explicitly a 24-hour clock, unlike bare '4' or '4:00'.
    if hour == 0 or hour > 12 or (len(match[1]) == 2 and match[2] is not None):
        return [hour * 60 + minute]
    return [hour % 12 * 60 + minute, (hour % 12 + 12) * 60 + minute]


def wall_instants(day, minute, zone):
    naive = datetime.combine(day, time()) + timedelta(minutes=minute)
    result = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(UTC)
        back = utc.astimezone(zone)
        if back.replace(tzinfo=None) == naive and utc not in result:
            result.append(utc)
    return sorted(result)


def day_start(day, zone):
    # Some zones advance at midnight. Use the first actual minute of the local day.
    for minute in range(1440):
        candidates = wall_instants(day, minute, zone)
        if candidates:
            return candidates[0]
    raise ValueError("This local calendar date does not exist.")


def clarification(code, field, choices=None):
    return {
        "state": "needs_clarification",
        "reason": code,
        "question": {"field": field, "choices": choices or []},
    }


def resolve(request, preferences, anchor):
    zone_name = request.timezone or preferences.timezone
    zone = ZoneInfo(zone_name)
    duration = request.duration_minutes or preferences.default_duration_minutes
    assumptions = [
        {
            "field": "timezone",
            "value": zone_name,
            "source": "explicit" if request.timezone else "saved_preferences",
        },
        {
            "field": "duration_minutes",
            "value": duration,
            "source": "explicit" if request.duration_minutes else "saved_preferences",
        },
    ]
    if request.date in {"today", "tomorrow"}:
        day = anchor.astimezone(zone).date() + timedelta(days=request.date == "tomorrow")
    else:
        day = date.fromisoformat(request.date)
    try:
        start, end = day_start(day, zone), day_start(day + timedelta(days=request.days), zone)
    except (ValueError, OverflowError):
        return clarification("date_does_not_exist", "date")
    exact = request.at_time is not None
    if exact:
        clocks = parse_clock(request.at_time, request.meridiem)
        context = request.time_context
        if context:
            clocks = [
                minute for minute in clocks if context.start_minute <= minute < context.end_minute
            ]
            if not clocks:
                return clarification("time_context_conflict", "at_time")
        if len(clocks) > 1:
            alternatives = [wall_instants(day, minute, zone) for minute in clocks]
            # A DST gap must not itself select AM/PM. Working hours disambiguate
            # only when both clock interpretations exist and exactly one fits.
            fitting = [
                minute
                for minute, instants in zip(clocks, alternatives, strict=True)
                if any(
                    fits_working(at, at + timedelta(minutes=duration), preferences)
                    for at in instants
                )
            ]
            if all(alternatives) and len(fitting) == 1:
                clocks = fitting
                assumptions.append(
                    {
                        "field": "meridiem",
                        "value": "AM" if clocks[0] < 720 else "PM",
                        "source": "saved_working_hours",
                    }
                )
            else:
                return clarification("ambiguous_meridiem", "meridiem", ["AM", "PM"])
        elif len(parse_clock(request.at_time, request.meridiem)) > 1:
            assumptions.append(
                {
                    "field": "meridiem",
                    "value": "AM" if clocks[0] < 720 else "PM",
                    "source": "explicit_time_context",
                }
            )
        instants = wall_instants(day, clocks[0], zone)
        if not instants:
            return clarification("nonexistent_local_time", "at_time")
        if len(instants) == 2 and request.fold is None:
            return clarification(
                "ambiguous_local_time",
                "fold",
                [
                    {
                        "fold": index,
                        "start": at.isoformat(),
                        "local": at.astimezone(zone).isoformat(),
                    }
                    for index, at in enumerate(instants)
                ],
            )
        if len(instants) == 1 and request.fold == 1:
            return clarification("fold_not_applicable", "fold")
        start = instants[request.fold or 0]
        end = start + timedelta(minutes=duration)
        if request.fold is not None:
            assumptions.append({"field": "fold", "value": request.fold, "source": "explicit"})
    try:
        evidence_start = start - timedelta(minutes=preferences.buffer_after_minutes)
        evidence_end = end + timedelta(minutes=preferences.buffer_before_minutes)
    except OverflowError:
        return clarification("date_does_not_exist", "date")
    return {
        "state": "resolved",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": zone_name,
        "duration_minutes": duration,
        "exact": exact,
        "assumptions": assumptions,
        "evidence_start": evidence_start.isoformat(),
        "evidence_end": evidence_end.isoformat(),
    }
