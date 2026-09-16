"""Fixed Google REST reads with bounded pages, response sizes and interval counts."""

import asyncio
import json

import httpx

from app.api.errors import ApiError
from app.schemas.calendar import CalendarCoverage, parse_instant

BASE = "https://www.googleapis.com/calendar/v3"
READ_ROLES = {"freeBusyReader", "reader", "writer", "writerWithoutPrivateAccess", "owner"}
MAX_PAGES = 10
MAX_LIST_ENTRIES = 1000
MAX_INTERVALS = 2000  # Across the entire response, before clipping/merging.
MAX_RESPONSE_BYTES = 2_000_000


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
    try:
        async with asyncio.timeout(15):
            body = await _request(
                "POST",
                "/freeBusy",
                token,
                transport=transport,
                json={
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                    "timeZone": "UTC",
                    "calendarExpansionMax": 10,
                    "items": [{"id": cid} for cid in ids],
                },
            )
    except TimeoutError:
        raise ApiError(503, "calendar_unavailable", "Google Calendar is unavailable.") from None
    return normalize(body, ids, start, end)
