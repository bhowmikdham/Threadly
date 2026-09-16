"""Pure UTC interval arithmetic with explicit local working-hour membership.

Working windows are constructed at minute resolution (the preference contract's
precision). Both DST folds are represented; nonexistent local minutes never occur.
No inference, provider access or current-clock reads belong in this module.
"""

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from app.schemas.calendar import Preferences, parse_instant
from app.schemas.slots import STEP_MINUTES

MINUTE = timedelta(minutes=1)


def merge(intervals):
    result = []
    normalized = ((parse_instant(lo), parse_instant(hi)) for lo, hi in intervals)
    for lo, hi in sorted(normalized):
        if lo >= hi:
            continue
        if result and lo <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], hi))
        else:
            result.append((lo, hi))
    return result


def working_windows(start, end, preferences: Preferences):
    """Convert local weekly membership into bounded continuous UTC windows."""
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or not timedelta(0) < end - start <= timedelta(days=16)
    ):
        raise ValueError("Working-hour query must be aware, positive and bounded.")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    zone = ZoneInfo(preferences.timezone)
    week = [bytearray(1440) for _ in range(7)]
    for period in preferences.working_periods:
        week[period.weekday][period.start_minute : period.end_minute] = b"\1" * (
            period.end_minute - period.start_minute
        )
    cursor = start.replace(second=0, microsecond=0)
    intervals = []
    while cursor < end:
        local = cursor.astimezone(zone)
        if week[local.weekday()][local.hour * 60 + local.minute]:
            lo, hi = max(cursor, start), min(cursor + MINUTE, end)
            if intervals and intervals[-1][1] == lo:
                intervals[-1] = (intervals[-1][0], hi)
            else:
                intervals.append((lo, hi))
        cursor += MINUTE
    return intervals


def fits_working(start, end, preferences):
    return any(lo <= start and end <= hi for lo, hi in working_windows(start, end, preferences))


def expanded_busy(calendars, preferences):
    before = timedelta(minutes=preferences.buffer_before_minutes)
    after = timedelta(minutes=preferences.buffer_after_minutes)
    return merge(
        (parse_instant(item.start) - before, parse_instant(item.end) + after)
        for calendar in calendars
        for item in calendar.busy
    )


def feasible_starts(
    start, end, duration_minutes, preferences, calendars, evaluation_time, *, exact=False
):
    """Return ALL feasible candidates; the caller separately limits displayed options.

    Adding busy time can remove candidates, never create new feasibility. Displaying
    the earliest three can of course expose a previously fourth-ranked candidate.
    """
    if not 5 <= duration_minutes <= 480:
        raise ValueError("Unsupported duration.")
    if not calendars or any(item.status != "known" for item in calendars):
        raise ValueError("Complete calendar coverage is required.")
    if any(item.tzinfo is None for item in (start, end, evaluation_time)):
        raise ValueError("Availability requires aware instants.")
    start, end, evaluation_time = (item.astimezone(UTC) for item in (start, end, evaluation_time))
    windows = working_windows(start, end, preferences)
    busy = expanded_busy(calendars, preferences)
    duration = timedelta(minutes=duration_minutes)
    notice = evaluation_time + timedelta(minutes=preferences.minimum_notice_minutes)
    zone = ZoneInfo(preferences.timezone)
    candidates = []
    index = 0
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += MINUTE
    while cursor + duration <= end:
        local = cursor.astimezone(zone)
        if (exact or local.minute % STEP_MINUTES == 0) and cursor >= notice:
            finish = cursor + duration
            while index < len(busy) and busy[index][1] <= cursor:
                index += 1
            blocked = index < len(busy) and busy[index][0] < finish
            if not blocked and any(lo <= cursor and finish <= hi for lo, hi in windows):
                candidates.append(cursor)
        if exact:
            break
        cursor += MINUTE
    return candidates


def select_options(candidates, duration_minutes, count):
    selected = []
    duration = timedelta(minutes=duration_minutes)
    for candidate in candidates:
        if not selected or candidate >= selected[-1] + duration:
            selected.append(candidate)
            if len(selected) == count:
                break
    return selected
