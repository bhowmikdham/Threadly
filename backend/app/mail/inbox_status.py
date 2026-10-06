"""Check today's arrivals independently of a bounded latest-message search."""

import asyncio
from datetime import UTC
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.assistant import inbox_chat
from app.schemas.inbox_chat import InboxFilters, InboxTodayCheck

TODAY_CHECK_SECONDS = 10


async def check_today(owner, filters, reference_at):
    local = reference_at.astimezone(ZoneInfo(filters.timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    result = InboxTodayCheck(
        local_date=local.date().isoformat(),
        received_from=start,
        received_before=reference_at,
        **{
            key: getattr(filters, key)
            for key in ("timezone", "folder", "inbox_category", "query", "sender_email")
        },
    )
    if start >= reference_at:
        return result.model_copy(update={"reason": "empty_time_window"}).model_dump(mode="json")
    today_filters = InboxFilters.model_validate(
        {
            **filters.model_dump(),
            "received_from": start,
            "received_before": reference_at,
            "limit": 1,
        }
    )
    try:
        async with asyncio.timeout(TODAY_CHECK_SECONDS):
            page = await inbox_chat.search(owner, today_filters, None)
    except TimeoutError:
        return result.model_copy(update={"reason": "read_timeout"}).model_dump(mode="json")
    except ApiError as exc:
        # Authentication, ownership and account-generation failures must stop
        # the turn. A provider outage can leave the already-read latest usable.
        if exc.status < 500:
            raise
        return result.model_copy(update={"reason": "provider_unavailable"}).model_dump(mode="json")
    exhausted = page["coverage"].get("provider_exhausted") is True and not page["next_cursor"]
    result.coverage_complete = exhausted
    if page["results"]:
        result.status = "has_messages"
    elif exhausted:
        result.status = "no_messages"
    else:
        result.reason = "incomplete_search"
    return result.model_dump(mode="json")
