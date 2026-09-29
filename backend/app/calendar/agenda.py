"""On-demand, owner-scoped Calendar agenda reads. No event cache or bulk sync."""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import client, service
from app.calendar.time_resolution import day_start
from app.capabilities.service import build_capabilities
from app.db.engine import get_session_factory
from app.db.models import CalendarPreference
from app.schemas.calendar import (
    AgendaCalendar,
    AgendaOut,
    AgendaPeriod,
    CalendarEventsOut,
    parse_instant,
)

MAX_RETURNED_EVENTS = 50
EVENT_ROLES = {"reader", "writer", "writerWithoutPrivateAccess", "owner"}


def window(period: AgendaPeriod, anchor: datetime, zone_name: str):
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("A timezone-aware anchor is required")
    zone = ZoneInfo(zone_name)
    day = anchor.astimezone(zone).date()
    if period == "tomorrow":
        day += timedelta(days=1)
    elif period == "this_week":
        day -= timedelta(days=day.weekday())
    elif period not in {"today", "next_7_days"}:
        raise ValueError("Unsupported agenda period")
    days = 7 if period in {"this_week", "next_7_days"} else 1
    return day_start(day, zone), day_start(day + timedelta(days=days), zone)


def require_events(user):
    caps = {item["id"]: item for item in build_capabilities(user)["capabilities"]}
    if not caps["calendar_events_read"]["ready"]:
        raise ApiError(
            403,
            "calendar_events_access_required",
            "Connect Calendar event read access to see your agenda.",
        )


def unknown(calendar_id, name, reason):
    return AgendaCalendar(
        calendar_id=calendar_id,
        name=name,
        status="unknown",
        reason=reason,
        events=[],
    )


def event_order(event, zone_name):
    if event.all_day:
        return day_start(date.fromisoformat(event.start), ZoneInfo(zone_name))
    return parse_instant(event.start)


def render(result: AgendaOut):
    """Deterministic chat response from checked events, without model-authored claims."""
    labels = {
        "today": "today",
        "tomorrow": "tomorrow",
        "this_week": "this week",
        "next_7_days": "over the next 7 days",
    }
    events = sorted(
        (
            (event_order(event, result.timezone), item.name, event)
            for item in result.calendars
            for event in item.events
        ),
        key=lambda row: row[0],
    )
    if not events and result.coverage == "complete":
        return f"I found no events on your selected calendars {labels[result.period]}."
    if not events:
        return (
            f"I couldn't confirm your agenda {labels[result.period]} because Calendar "
            "coverage was incomplete. Reconnect Calendar or check your selected calendars."
        )
    zone = ZoneInfo(result.timezone)
    lines = [
        f"Here is what I found on your selected calendars {labels[result.period]} "
        f"({result.timezone}):"
    ]
    for _, calendar_name, event in events[:10]:
        if event.all_day:
            when = event.start + " (all day)"
        else:
            when = (
                datetime.fromisoformat(event.start).astimezone(zone).strftime("%a %d %b, %I:%M %p")
            )
        title = " ".join(event.summary.split())
        location = " — " + " ".join(event.location.split()) if event.location else ""
        name = " ".join(calendar_name.split()) or "Selected calendar"
        lines.append(f"• {when}: {title}{location} [{name}]")
    if len(events) > 10:
        lines.append(f"Showing 10 of {len(events)} returned events.")
    if result.coverage != "complete":
        lines.append("Calendar coverage is incomplete; there may be more events.")
    return "\n".join(lines)


async def read(user_id, period: AgendaPeriod = "today", *, transport=None, anchor=None):
    """Fetch only the selected calendars and a single bounded page from each."""
    return await _read(user_id, period, transport=transport, anchor=anchor)


async def search(user_id, resolve_window, *, query="", transport=None, anchor=None):
    return await _read(
        user_id,
        None,
        resolve_window=resolve_window,
        query=query,
        transport=transport,
        anchor=anchor,
    )


async def _read(user_id, period, *, resolve_window=None, query="", transport=None, anchor=None):
    async with get_session_factory()() as session:
        require_events(await service.account(session, user_id))
    token, account_version = await service._snapshot(user_id)
    async with get_session_factory()() as session:
        user = await service.account(session, user_id, expected=account_version)
        require_events(user)
        pref = await session.get(CalendarPreference, user_id)
        service.check_pref(pref, pref.version if pref else 0, account_version)
        preference_version = pref.version
        selected = list(pref.preferences["calendar_ids"])
        timezone = pref.preferences["timezone"]
        checked_at = anchor or await session.scalar(select(func.clock_timestamp()))
    start, end = (
        resolve_window(checked_at, timezone)
        if resolve_window
        else window(period, checked_at, timezone)
    )
    # ACLs can change independently of OAuth grants. Never query an unselected ID.
    calendars = await client.list_calendars(token, transport=transport)
    visible = {item["id"]: item for item in calendars}

    semaphore = asyncio.Semaphore(4)

    async def one(calendar_id):
        entry = visible.get(calendar_id)
        name = entry["summary"] if entry else ""
        if entry is None or entry["access_role"] not in EVENT_ROLES:
            return unknown(calendar_id, name, "not_accessible")
        async with semaphore:
            try:
                return await client.list_events(
                    token,
                    calendar_id,
                    name,
                    start,
                    end,
                    transport=transport,
                    **({"query": query} if query else {}),
                )
            except ApiError as exc:
                return unknown(
                    calendar_id,
                    name,
                    "not_accessible" if exc.code == "calendar_access_denied" else "provider_error",
                )

    results = await asyncio.gather(*(one(cid) for cid in selected))
    ordered = sorted(
        (
            (event_order(event, timezone), index, event)
            for index, item in enumerate(results)
            for event in item.events
        ),
        key=lambda row: (row[0], row[1]),
    )
    if len(ordered) > MAX_RETURNED_EVENTS:
        retained = {id(event) for _, _, event in ordered[:MAX_RETURNED_EVENTS]}
        for item in results:
            if any(id(event) not in retained for event in item.events):
                item.events = [event for event in item.events if id(event) in retained]
                if item.status == "known":
                    item.status, item.reason = "partial", "result_limit"
    # An account/scope/preference change during Google IO invalidates the whole read.
    async with get_session_factory()() as session:
        user = await service.account(session, user_id, lock=True, expected=account_version)
        require_events(user)
        pref = await session.get(CalendarPreference, user_id, with_for_update=True)
        service.check_pref(pref, preference_version, account_version)
        current = await session.scalar(select(func.clock_timestamp()))
        if current - checked_at > timedelta(minutes=2):
            raise ApiError(409, "calendar_read_expired", "The agenda check expired; retry.")
    statuses = {item.status for item in results}
    coverage = (
        "complete" if statuses == {"known"} else "unknown" if statuses == {"unknown"} else "partial"
    )
    return (AgendaOut if period else CalendarEventsOut)(
        period=period or "custom",
        timezone=timezone,
        start=start,
        end=end,
        checked_at=checked_at,
        account_version=account_version,
        preferences_version=preference_version,
        coverage=coverage,
        calendars=results,
        total_returned=sum(len(item.events) for item in results),
    )
