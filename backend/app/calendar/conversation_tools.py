"""Backend-owned Calendar reads and answers for the conversational tool catalogue."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.calendar import agenda, availability, day_availability, service
from app.calendar.time_resolution import day_start, parse_clock, wall_instants
from app.schemas.calendar import FreeBusyRequest, parse_instant
from app.schemas.calendar_tools import CalendarWindow

POLICY = "calendar-conversation-reads-2.1.0"
MAX_DISPLAY = 10
WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|thurday|friday|saturday|sunday)"
DAY_PATTERN = rf"(?:(?:this|next) )?{WEEKDAY}(?: (?:this|next) week)?"
DATE_PATTERN = (
    rf"(?:next 7 days|this week|next week|today|tomorrow|\d{{4}}-\d{{2}}-\d{{2}}|{DAY_PATTERN})"
)


class RequestClarification(ValueError):
    """Safe, specific instructions for recovering a bounded read request."""


def normalized(value):
    return " ".join(value.casefold().split())


def literal(value, instruction):
    value = normalized(value)
    if not value or not re.search(
        r"(?<!\w)" + re.escape(value) + r"(?!\w)", normalized(instruction)
    ):
        raise RequestClarification(
            "Please specify the date, time window or search words for this Calendar read"
        )
    return value


def validate_scope(args, instruction):
    """Check source-bound parameters and prevent silently dropping temporal qualifiers."""
    remainder = normalized(instruction)
    for field in ("query", "date_phrase", "start_time", "end_time", "at_time", "duration_phrase"):
        value = getattr(args, field, "")
        if field in {"start_time", "end_time", "at_time"}:
            source = getattr(args, field + "_source", "")
            if source:
                # Typed clocks must preserve the same instant, not merely quote some text.
                if parse_clock(source) != parse_clock(value):
                    raise RequestClarification("The clock interpretation conflicts with your words")
                value = source
        if field == "date_phrase" and args.date_source:
            value = args.date_source
            # A source quote anchors a semantic date interpretation, but may not
            # swallow separate clock, timezone, duration or external-action constraints.
            if re.search(
                r"\b(?:morning|afternoon|evening|noon|midnight|before|until|"
                r"utc|gmt|timezone|hours?|minutes?|mins?|send|book|create|update|delete|"
                r"reschedule|cancel|invite)\b|\d{1,2}:\d{2}|\d\s*[ap]\.?\s*m\b|/",
                value.casefold(),
            ):
                raise RequestClarification(
                    "Keep clock and other constraints separate from the date"
                )
        if value:
            # One field cannot consume another field's source span (for example,
            # a query containing the date must not hide a dropped time qualifier).
            literal(value, remainder)
            remainder = re.sub(
                r"(?<!\w)" + re.escape(normalized(value)) + r"(?!\w)", " ", remainder
            )
    # These are temporal/operation constraints, not intent classification. A semantic
    # tool selection cannot discard them when its narrower schema cannot represent them.
    if re.search(
        rf"\b{DATE_PATTERN}\b|\b(?:next|last|previous|following|yesterday|tonight|"
        r"morning|afternoon|evening|weekend|month|year|noon|midnight|before|after|until|"
        r"utc|gmt|timezone|hours?|minutes?|mins?|send|book|create|update|delete|"
        r"reschedule|cancel|invite)\b|\b(?:at|from|to|between|on)\s+\d|"
        r"\d{1,2}:\d{2}|\d\s*[ap]\.?\s*m\b|[A-Za-z]+/[A-Za-z_]+",
        remainder,
    ):
        raise RequestClarification(
            "Please specify the full date and time window; I couldn't preserve all "
            "your constraints in this read. Event changes need a separate reviewed request"
        )


def resolve_window(args, instruction, anchor, timezone, *, date_anchor=None):
    validate_scope(args, instruction)
    # The model interprets language; only this canonical tool value is parsed.
    # validate_scope already binds its separate source quote to user-authored text.
    phrase = normalized(args.date_phrase)
    zone = ZoneInfo(timezone)
    today = (date_anchor or anchor).astimezone(zone).date()
    span = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (?:to|through) (\d{4}-\d{2}-\d{2})", phrase)
    if args.date is not None:
        meaning = args.date
        if meaning.kind == "relative":
            first = today + timedelta(days=meaning.offset_days)
            last = first + timedelta(days=meaning.days)
        elif meaning.kind == "weekday":
            offset = meaning.weekday - today.weekday()
            if meaning.week == "upcoming":
                offset %= 7
            elif meaning.week == "next":
                offset += 7
            first = today + timedelta(days=offset)
            last = first + timedelta(days=1)
        elif meaning.kind == "week":
            first = (
                today
                - timedelta(days=today.weekday())
                + timedelta(days=7 if meaning.week == "next" else 0)
            )
            last = first + timedelta(days=7)
        else:
            first = date.fromisoformat(meaning.start)
            last = date.fromisoformat(meaning.end or meaning.start) + timedelta(days=1)
    elif span:
        first, last = date.fromisoformat(span[1]), date.fromisoformat(span[2]) + timedelta(days=1)
    elif phrase in {"this week", "next week"}:
        first = (
            today
            - timedelta(days=today.weekday())
            + timedelta(days=7 if phrase == "next week" else 0)
        )
        last = first + timedelta(days=7)
    elif phrase == "next 7 days":
        first, last = today, today + timedelta(days=7)
    elif re.fullmatch(DAY_PATTERN, phrase):
        # Explicit 'next' means the following local Monday-Sunday week, shown in the answer.
        if "this" in phrase and "next" in phrase:
            raise RequestClarification("Conflicting week qualifiers")
        if "next" in phrase:
            weekday = next(day for day in day_availability.WEEKDAYS if day in phrase)
            first = (
                today
                - timedelta(days=today.weekday())
                + timedelta(days=7 + day_availability.WEEKDAYS.index(weekday))
            )
        else:
            first = day_availability.resolve_day(phrase, date_anchor or anchor, timezone)
        last = first + timedelta(days=1)
    elif relative := re.fullmatch(r"in (\d{1,2}) days", phrase):
        if not 0 <= int(relative[1]) <= 14:
            raise RequestClarification("Choose a relative day within the next fourteen days")
        first = today + timedelta(days=int(relative[1]))
        last = first + timedelta(days=1)
    elif phrase in {"today", "tomorrow"} or re.fullmatch(r"\d{4}-\d{2}-\d{2}", phrase):
        first = day_availability.resolve_day(phrase, date_anchor or anchor, timezone)
        last = first + timedelta(days=1)
    else:
        raise RequestClarification("Unsupported Calendar date")
    if not 1 <= (last - first).days <= 14:
        raise RequestClarification("Choose a date window of at most fourteen days")
    start, end = day_start(first, zone), day_start(last, zone)
    if args.start_time:
        if last - first != timedelta(days=1):
            raise RequestClarification("A clock window needs a single date")
        instants = []
        for value in (args.start_time, args.end_time):
            clocks = parse_clock(value)
            if len(clocks) != 1:
                raise RequestClarification("Please specify AM/PM for both clock times")
            candidates = wall_instants(first, clocks[0], zone)
            if len(candidates) != 1:
                raise RequestClarification("This local time is ambiguous or does not exist")
            instants.append(candidates[0])
        start, end = instants
    if start >= end or start < anchor - timedelta(days=31) or end > anchor + timedelta(days=90):
        raise RequestClarification("Choose an ordered window within the supported Calendar horizon")
    return start, end


def resolve_single_start(args, instruction, preferences, anchor, date_anchor=None):
    validate_scope(args, instruction)
    trimmed = instruction
    for value in (args.at_time_source, args.duration_phrase):
        if value:
            quoted = literal(value, trimmed)
            trimmed = re.sub(r"(?<!\w)" + re.escape(quoted) + r"(?!\w)", " ", normalized(trimmed))
    window = CalendarWindow.model_validate(
        {
            key: value
            for key, value in args.model_dump().items()
            if key in CalendarWindow.model_fields
        }
    )
    lo, hi = resolve_window(window, trimmed, anchor, preferences.timezone, date_anchor=date_anchor)
    zone = ZoneInfo(preferences.timezone)
    if (hi.astimezone(zone).date() - lo.astimezone(zone).date()).days != 1:
        raise RequestClarification("Choose one day for that time")
    clocks = parse_clock(args.at_time)
    if len(clocks) != 1:
        raise RequestClarification("Is that AM or PM?")
    starts = wall_instants(lo.astimezone(zone).date(), clocks[0], zone)
    if len(starts) != 1:
        raise RequestClarification("That local time is ambiguous or does not exist")
    minutes = duration(args.duration_phrase, instruction, preferences.default_duration_minutes)
    start = starts[0]
    if start <= anchor:
        raise RequestClarification("Choose a future time to check")
    return start, start + timedelta(minutes=minutes), minutes


def duration(phrase, instruction, default):
    if not phrase:
        return default
    value = literal(phrase, instruction)
    named = {"half an hour": 30, "an hour": 60, "one hour": 60, "two hours": 120}
    if value in named:
        return named[value]
    match = re.fullmatch(r"(\d+)\s*(?:-\s*)?(minutes?|mins?|hours?|hrs?)", value)
    if not match:
        raise RequestClarification("Specify a duration in minutes or hours")
    minutes = int(match[1]) * (60 if match[2].startswith(("hour", "hr")) else 1)
    if not 5 <= minutes <= 480:
        raise RequestClarification("Duration must be between five minutes and eight hours")
    return minutes


def display(instant, timezone):
    return instant.astimezone(ZoneInfo(timezone)).strftime("%a %-d %b %Y, %-I:%M %p %Z")


def clean(value):
    return " ".join(value.split())


def event_bounds(event, timezone):
    if event.all_day:
        zone = ZoneInfo(timezone)
        return day_start(date.fromisoformat(event.start), zone), day_start(
            date.fromisoformat(event.end), zone
        )
    return parse_instant(event.start), parse_instant(event.end)


def render_events(result, *, overlaps=False, query=""):
    rows = sorted(
        [
            (event_bounds(event, result.timezone), cal.name, event)
            for cal in result.calendars
            for event in cal.events
        ],
        key=lambda item: item[0],
    )
    header = (
        f"Selected calendars, {display(result.start, result.timezone)} to "
        f"{display(result.end, result.timezone)} (end exclusive)."
    )
    lines = [header]
    if overlaps:
        pairs = [
            (left, right)
            for index, left in enumerate(rows)
            for right in rows[index + 1 :]
            if max(left[0][0], right[0][0], result.start) < min(left[0][1], right[0][1], result.end)
        ]
        for left, right in pairs[:MAX_DISPLAY]:
            start, end = (
                max(left[0][0], right[0][0], result.start),
                min(left[0][1], right[0][1], result.end),
            )
            lines.append(
                f"• {display(start, result.timezone)}–{display(end, result.timezone)}: "
                f"{clean(left[2].summary)} [{clean(left[1])}] overlaps "
                f"{clean(right[2].summary)} [{clean(right[1])}]."
            )
        count = len(pairs)
        if not count:
            lines.append("No overlaps found among the returned events.")
        lines.append(
            "Overlapping entries may include copies of the same meeting; "
            "they are not necessarily booking conflicts."
        )
    else:
        if query:
            lines.append(f"Search: {clean(query)}.")
        for (start, end), name, event in rows[:MAX_DISPLAY]:
            when = (
                event.start + " (all day)"
                if event.all_day
                else f"{display(start, result.timezone)}–{display(end, result.timezone)}"
            )
            location = f" — {clean(event.location)}" if event.location else ""
            lines.append(f"• {when}: {clean(event.summary)}{location} [{clean(name)}]")
        count = len(rows)
        if not count:
            lines.append(
                "No matching events were returned." if query else "No events were returned."
            )
    if count > MAX_DISPLAY:
        lines.append(
            f"Showing {MAX_DISPLAY} of {count} returned "
            f"{'overlaps' if overlaps else 'events'}; narrow the date window to see more."
        )
    if result.coverage != "complete":
        lines.append(
            "Calendar coverage is incomplete; additional events or overlaps may be missing. "
            "Narrow the window or check Calendar settings."
        )
    return "\n".join(lines)


async def execute(owner, name, args, instruction, *, anchor=None, date_anchor=None):
    """Terminal, deterministic responses: provider prose never controls another tool call."""
    anchor = anchor or datetime.now(UTC)
    if getattr(args, "subject", None) == "other":
        return {
            "kind": "message",
            "text": (
                "I can check your selected calendars, but I don't have access "
                "to that person's availability."
            ),
        }
    try:
        if name == "list_calendars":
            result = await service.list_calendars(owner)
            rows = result["calendars"]
            lines = ["Calendars available on your connected account:"]
            for row in rows[:MAX_DISPLAY]:
                access = (
                    "editable"
                    if row.get("event_write_acl")
                    else "read-only"
                    if row.get("access_role") == "reader"
                    else "busy-time access"
                    if row["can_read_busy"]
                    else "not readable"
                )
                lines.append(f"• {clean(row['summary']) or 'Untitled calendar'} ({access})")
            if not rows:
                lines.append("No calendars were returned.")
            if len(rows) > MAX_DISPLAY:
                lines.append(
                    f"Showing {MAX_DISPLAY} of {len(rows)} calendars. "
                    "See Calendar settings for the full list."
                )
            return {
                "kind": "message",
                "text": "\n".join(lines),
                "calendar_tools": {
                    "operation": name,
                    "checked_at": result["checked_at"].isoformat(),
                },
            }
        if name in {"search_calendar_events", "find_overlapping_events"}:
            validate_scope(args, instruction)
            result = await agenda.search(
                owner,
                lambda checked, zone: resolve_window(
                    args, instruction, anchor, zone, date_anchor=date_anchor
                ),
                query=getattr(args, "query", ""),
            )
            return {
                "kind": "message",
                "text": render_events(
                    result,
                    overlaps=name == "find_overlapping_events",
                    query=getattr(args, "query", ""),
                ),
                "calendar_tools": {
                    "operation": name,
                    "coverage": result.coverage,
                    "checked_at": result.checked_at.isoformat(),
                },
            }
        pref = await service.get_preferences(owner)
        preferences = pref.preferences
        is_single = name == "check_time_availability"
        if is_single:
            start, end, single_minutes = resolve_single_start(
                args, instruction, preferences, anchor, date_anchor
            )
        else:
            start, end = resolve_window(
                args, instruction, anchor, preferences.timezone, date_anchor=date_anchor
            )
        if end <= anchor:
            raise RequestClarification("Availability requires a future window")
        start = max(start, anchor)
        is_free = name == "find_free_times"
        minutes = (
            duration(args.duration_phrase, instruction, preferences.default_duration_minutes)
            if is_free
            else None
        )
        before = timedelta(minutes=preferences.buffer_after_minutes if is_free else 0)
        after = timedelta(minutes=preferences.buffer_before_minutes if is_free else 0)
        evidence = await service.query_freebusy(
            owner,
            FreeBusyRequest(
                expected_preferences_version=pref.version,
                start=start - before,
                end=end + after,
            ),
        )
        if (
            evidence.preferences_version != pref.version
            or evidence.account_version != pref.account_version
        ):
            raise service.conflict()
        complete = (
            evidence.coverage == "complete"
            and bool(evidence.calendars)
            and all(c.status == "known" for c in evidence.calendars)
        )
        lines = [
            f"Your selected calendars only, {display(start, preferences.timezone)} "
            f"to {display(end, preferences.timezone)}. "
            "Other people's availability has not been checked."
        ]
        if is_single:
            duration_source = "requested" if args.duration_phrase else "saved default"
            lines.append(f"Checking your {duration_source} {single_minutes}-minute duration.")
        if is_free:
            lines.append(
                f"Using {minutes}-minute meetings and your saved working hours, "
                "buffers and minimum notice."
            )
            if complete:
                candidates = availability.feasible_starts(
                    start, end, minutes, preferences, evidence.calendars, evidence.checked_at
                )
                options = availability.select_options(candidates, minutes, args.limit)
                lines += [
                    f"• {display(at, preferences.timezone)}–"
                    f"{display(at + timedelta(minutes=minutes), preferences.timezone)}"
                    for at in options
                ]
                if not options:
                    lines.append("No free slots fit those settings in this window.")
                lines.append("These times are not reserved.")
        else:
            periods = availability.merge(
                (max(b.start, start), min(b.end, end))
                for c in evidence.calendars
                if c.status == "known"
                for b in c.busy
            )
            lines += [
                f"• Busy: {display(lo, preferences.timezone)}–{display(hi, preferences.timezone)}"
                for lo, hi in periods[:MAX_DISPLAY]
            ]
            if not periods and complete:
                lines.append("No busy time is recorded in this window.")
            if len(periods) > MAX_DISPLAY:
                lines.append(f"Showing {MAX_DISPLAY} of {len(periods)} busy periods.")
        if not complete:
            lines.append(
                "Calendar coverage is incomplete. I cannot confirm free time; "
                "unchecked calendars may contain additional busy periods. "
                "Check Calendar settings or retry."
            )
        return {
            "kind": "message",
            "text": "\n".join(lines),
            **(
                {"error_code": "calendar_coverage_incomplete"} if is_single and not complete else {}
            ),
            "calendar_tools": {
                "operation": name,
                **(
                    {
                        "date": start.astimezone(ZoneInfo(preferences.timezone)).date().isoformat(),
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "timezone": preferences.timezone,
                        "duration_minutes": single_minutes,
                    }
                    if is_single
                    else {}
                ),
                "coverage": evidence.coverage,
                "evidence_id": evidence.id,
                "checked_at": evidence.checked_at.isoformat(),
                "expires_at": evidence.expires_at.isoformat(),
            },
        }
    except RequestClarification as exc:
        return {"kind": "clarification", "text": str(exc) + "."}
    except (ValueError, OverflowError):
        return {
            "kind": "clarification",
            "text": "Please specify a date or a range of up to 14 days and preserve all time "
            "constraints. For a time window, include both ends with AM/PM. "
            "I'll use your saved Calendar timezone.",
        }
    except ApiError as exc:
        return {
            "kind": "message",
            "text": "I couldn't complete the Calendar read. "
            + (
                "Review your Calendar connection and selections in Calendar settings, "
                "then try again."
                if exc.code.startswith("calendar_")
                else "Please try again."
            ),
            "error_code": exc.code,
        }
