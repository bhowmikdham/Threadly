"""Fixed Google REST reads with bounded pages, response sizes and interval counts."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.api.errors import ApiError
from app.schemas.calendar import AgendaCalendar, AgendaEvent, CalendarCoverage, parse_instant

BASE = "https://www.googleapis.com/calendar/v3"
READ_ROLES = {"freeBusyReader", "reader", "writer", "writerWithoutPrivateAccess", "owner"}
MAX_PAGES = 10
MAX_LIST_ENTRIES = 1000
MAX_INTERVALS = 2000  # Across the entire response, before clipping/merging.
MAX_RESPONSE_BYTES = 2_000_000
MAX_AGENDA_ITEMS_PER_CALENDAR = 25


def invalid():
    return ApiError(502, "calendar_response_invalid", "Google Calendar returned invalid data.")


async def _request(method, path, token, *, transport=None, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            async with client.stream(
                method, BASE + path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            ) as response:
                if response.status_code in (401, 403):
                    raise ApiError(
                        403, "calendar_access_denied", "Reconnect or check Calendar access."
                    )
                if response.status_code != 200:
                    raise ApiError(503, "calendar_unavailable", "Google Calendar is unavailable.")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise invalid()
        body = json.loads(content)
    except (httpx.HTTPError, TimeoutError):
        raise ApiError(503, "calendar_unavailable", "Google Calendar is unavailable.") from None
    except (ValueError, UnicodeError):
        raise invalid() from None
    if not isinstance(body, dict):
        raise invalid()
    return body


async def list_calendars(token, *, transport=None):
    items, seen = {}, set()
    page = None
    try:
        async with asyncio.timeout(30):
            for _ in range(MAX_PAGES):
                params = {"maxResults": 100, "showHidden": "true", "showDeleted": "false"}
                if page:
                    params["pageToken"] = page
                body = await _request(
                    "GET", "/users/me/calendarList", token, transport=transport, params=params
                )
                rows = body.get("items", [])
                if not isinstance(rows, list) or len(rows) > 100:
                    raise invalid()
                for row in rows:
                    if not isinstance(row, dict):
                        raise invalid()
                    cid, role, title = row.get("id"), row.get("accessRole"), row.get("summary", "")
                    if (
                        not isinstance(cid, str)
                        or not 0 < len(cid) <= 1024
                        or cid != cid.strip()
                        or any(ord(c) < 32 or ord(c) == 127 for c in cid)
                        or cid in items
                        or not isinstance(role, str)
                        or not isinstance(title, str)
                        or len(title) > 1024
                        or ("deleted" in row and type(row["deleted"]) is not bool)
                    ):
                        raise invalid()
                    items[cid] = {
                        "id": cid,
                        "summary": title,
                        "access_role": role,
                        "can_read_busy": role in READ_ROLES and not row.get("deleted", False),
                        # ACL evidence only: this is not scope readiness or booking authorization.
                        "event_write_acl": role in {"writer", "writerWithoutPrivateAccess", "owner"}
                        and not row.get("deleted", False),
                    }
                if len(items) > MAX_LIST_ENTRIES:
                    raise invalid()
                page = body.get("nextPageToken")
                if page is None:
                    return list(items.values())
                if not isinstance(page, str) or not 0 < len(page) <= 4096 or page in seen:
                    raise invalid()
                seen.add(page)
    except TimeoutError:
        raise ApiError(503, "calendar_unavailable", "Google Calendar is unavailable.") from None
    raise ApiError(502, "calendar_list_limit", "Calendar list exceeds the supported page limit.")


def normalize(body, ids, start, end):
    try:
        if parse_instant(body.get("timeMin")) != start or parse_instant(body.get("timeMax")) != end:
            raise invalid()
    except ValueError:
        raise invalid() from None
    calendars = body.get("calendars")
    if not isinstance(calendars, dict):
        raise invalid()
    count = 0
    result = []
    for cid in ids:
        item, reason, intervals = calendars.get(cid), None, []
        if item is None:
            reason = "missing"
        elif not isinstance(item, dict):
            reason = "malformed"
        elif "errors" in item and item["errors"] != []:
            reason = "provider_error"
        elif not isinstance(item.get("busy"), list):
            reason = "malformed"
        else:
            count += len(item["busy"])
            if count > MAX_INTERVALS:
                raise ApiError(502, "calendar_interval_limit", "Calendar interval limit exceeded.")
            for interval in item["busy"]:
                try:
                    if not isinstance(interval, dict):
                        raise ValueError()
                    lo, hi = (
                        parse_instant(interval.get("start")),
                        parse_instant(interval.get("end")),
                    )
                    if lo >= hi:
                        raise ValueError()
                    lo, hi = max(lo, start), min(hi, end)
                    if lo < hi:
                        intervals.append((lo, hi))
                except ValueError:
                    reason = "malformed"
                    break
        merged = []
        if reason is None:
            for lo, hi in sorted(intervals):
                if merged and lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
        result.append(
            CalendarCoverage(
                calendar_id=cid,
                status="unknown" if reason else "known",
                reason=reason,
                busy=[{"start": lo, "end": hi} for lo, hi in merged],
            )
        )
    return result


async def freebusy(token, ids, start, end, *, transport=None):
    # Google echoes milliseconds, while current-time anchors include microseconds.
    # Whole-second bounds cover the full request without depending on rounding.
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    query_start = start.replace(microsecond=0)
    query_end = end.replace(microsecond=0)
    if end.microsecond:
        query_end += timedelta(seconds=1)
    try:
        async with asyncio.timeout(15):
            body = await _request(
                "POST",
                "/freeBusy",
                token,
                transport=transport,
                json={
                    "timeMin": query_start.isoformat(),
                    "timeMax": query_end.isoformat(),
                    "timeZone": "UTC",
                    "calendarExpansionMax": 10,
                    "items": [{"id": cid} for cid in ids],
                },
            )
    except TimeoutError:
        raise ApiError(503, "calendar_unavailable", "Google Calendar is unavailable.") from None
    # Validate the exact transmitted window; never accept a shifted/narrower echo.
    result = normalize(body, ids, query_start, query_end)
    for calendar in result:
        calendar.busy = [
            interval.model_copy(update={
                "start": max(interval.start, start), "end": min(interval.end, end),
            })
            for interval in calendar.busy
            if interval.start < end and interval.end > start
        ]
    return result


def _agenda_event(row, fallback_zone=None):
    if not isinstance(row, dict):
        raise ValueError("Invalid event")
    if row.get("status") == "cancelled":
        return None
    status = row.get("status", "confirmed")
    if status not in {"confirmed", "tentative"}:
        raise ValueError("Invalid event status")
    start, end = row.get("start"), row.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ValueError("Missing event time")
    all_day = "date" in start and "date" in end
    if all_day:
        if set(start) & {"dateTime"} or set(end) & {"dateTime"}:
            raise ValueError("Mixed event time")
        begin, finish = date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
        if begin >= finish:
            raise ValueError("Invalid all-day range")
        first, last = begin.isoformat(), finish.isoformat()
    else:
        if "date" in start or "date" in end:
            raise ValueError("Mixed event time")
        begin, finish = _event_datetime(start, fallback_zone), _event_datetime(end, fallback_zone)
        if begin >= finish:
            raise ValueError("Invalid timed range")
        first, last = begin.isoformat(), finish.isoformat()
    visibility = row.get("visibility", "default")
    if visibility not in {"default", "public", "private", "confidential"}:
        raise ValueError("Invalid visibility")
    private = visibility in {"private", "confidential"}
    title, location = row.get("summary", ""), row.get("location")
    if not isinstance(title, str) or len(title) > 300:
        raise ValueError("Invalid summary")
    if location is not None and (not isinstance(location, str) or len(location) > 300):
        raise ValueError("Invalid location")
    summary = "Busy" if private else title.strip() or "Untitled event"
    if status == "tentative":
        summary = "Busy (tentative)" if private else "Tentative: " + summary
    return AgendaEvent(
        summary=summary,
        start=first,
        end=last,
        all_day=all_day,
        redacted=private,
        location=None if private else location,
    )


def _event_datetime(part, fallback_zone=None):
    """Resolve Google's offsetless dateTime only when its IANA zone is unambiguous."""
    raw = part.get("dateTime")
    if not isinstance(raw, str):
        raise ValueError("Missing event dateTime")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid event dateTime") from None
    if value.tzinfo is not None and value.utcoffset() is not None:
        return parse_instant(value)
    zone_name = part.get("timeZone", fallback_zone)
    if not isinstance(zone_name, str) or not zone_name:
        raise ValueError("Offsetless event needs timeZone")
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Invalid event timeZone") from None
    instants = set()
    for fold in (0, 1):
        local = value.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == value:
            instants.add(instant)
    if len(instants) != 1:
        # No instant in a DST gap, two in a repeated hour. Do not invent one.
        raise ValueError("Ambiguous or nonexistent event time")
    return instants.pop()


async def list_events(token, calendar_id, name, start, end, *, transport=None, query=""):
    """One bounded events.list page; a continuation is explicit partial coverage."""
    body = await _request(
        "GET",
        "/calendars/" + quote(calendar_id, safe="") + "/events",
        token,
        transport=transport,
        params={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            **({"q": query} if query else {}),
            "singleEvents": "true",
            "orderBy": "startTime",
            "showDeleted": "false",
            "maxResults": MAX_AGENDA_ITEMS_PER_CALENDAR,
            "fields": (
                "kind,timeZone,items(start,end,summary,location,status,visibility),nextPageToken"
            ),
        },
    )
    rows, more = body.get("items", []), body.get("nextPageToken")
    fallback_zone = body.get("timeZone")
    if body.get("kind") != "calendar#events":
        raise invalid()
    if fallback_zone is not None and (
        not isinstance(fallback_zone, str) or not 0 < len(fallback_zone) <= 100
    ):
        raise invalid()
    if not isinstance(rows, list) or len(rows) > MAX_AGENDA_ITEMS_PER_CALENDAR:
        raise invalid()
    if more is not None and (not isinstance(more, str) or not more):
        raise invalid()
    try:
        events = [event for row in rows if (event := _agenda_event(row, fallback_zone)) is not None]
    except (KeyError, TypeError, ValueError):
        raise invalid() from None
    return AgendaCalendar(
        calendar_id=calendar_id,
        name=name,
        status="partial" if more else "known",
        reason="result_limit" if more else None,
        events=events,
    )
