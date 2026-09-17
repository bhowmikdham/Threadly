"""Durable, idempotent slot queries around the B12 read seam; never creates events."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import availability, service, time_resolution
from app.db.engine import get_session_factory
from app.db.models import CalendarEvidence, CalendarPreference, CalendarSlotRequest
from app.schemas.calendar import FreeBusyOut, FreeBusyRequest, Preferences, parse_instant
from app.schemas.slots import (
    SLOT_POLICY,
    STEP_MINUTES,
    SlotOption,
    SlotRequest,
    SlotRequestOut,
    valid_zone,
)


def expired():
    return ApiError(409, "calendar_slots_expired", "Slot request expired; create a fresh request.")


def view(row):
    result = row.result or {}
    return SlotRequestOut(
        id=row.id,
        state=row.state,
        anchor_at=row.anchor_at,
        anchor_source="prior_request" if row.anchor_from_request_id else "request_received",
        anchor_from_request_id=row.anchor_from_request_id,
        preferences_version=row.preferences_version,
        account_version=row.account_version,
        policy_version=row.policy_version,
        created_at=row.created_at,
        expires_at=row.expires_at,
        resolution=row.resolution,
        evidence_id=row.evidence_id,
        calculated_at=row.calculated_at,
        slots=result.get("slots", []),
        reason=result.get("reason"),
        error_code=row.error_code,
    )


async def check_current(session, user_id, row):
    user = await service.account(session, user_id, lock=True, expected=row.account_version)
    pref = await session.get(CalendarPreference, user_id, with_for_update=True)
    service.check_pref(pref, row.preferences_version, user.google_account_version)
    if row.policy_version != SLOT_POLICY or pref.preferences != row.preferences:
        raise service.conflict()
    # Lock waits must not let a pre-lock timestamp revive an expired offer.
    now = await session.scalar(select(func.clock_timestamp()))
    if row.expires_at <= now:
        raise expired()
    if row.evidence_id:
        evidence = await session.scalar(
            select(CalendarEvidence).where(
                CalendarEvidence.id == row.evidence_id, CalendarEvidence.user_id == user_id
            )
        )
        if evidence is None or evidence.expires_at <= now:
            raise expired()


async def get_request(user_id, request_id):
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(CalendarSlotRequest).where(
                CalendarSlotRequest.id == request_id, CalendarSlotRequest.user_id == user_id
            )
        )
        if row is None:
            raise ApiError(404, "calendar_slot_request_missing", "Slot request not found.")
        # Snapshot is immutable once published; take account/preferences locks for freshness.
        await check_current(session, user_id, row)
        return view(row)


def prepare(body, preferences, anchor, now):
    resolution = time_resolution.resolve(body, preferences, anchor)
    if resolution["state"] != "resolved":
        return resolution
    start, end = parse_instant(resolution["start"]), parse_instant(resolution["end"])
    if not resolution["exact"]:
        # Today's elapsed hours are not needed; retain padding for recently ended meetings.
        start = max(start, now + timedelta(minutes=preferences.minimum_notice_minutes))
    if end <= start or (resolution["exact"] and start < now):
        return {**resolution, "state": "elapsed", "reason": "window_elapsed"}
    resolution["search_start"] = start.isoformat()
    resolution["evidence_start"] = (
        start - timedelta(minutes=preferences.buffer_after_minutes)
    ).isoformat()
    if end + timedelta(minutes=preferences.buffer_before_minutes) > now + timedelta(days=90):
        return time_resolution.clarification("window_out_of_bounds", "date")
    if start < now - timedelta(minutes=5):
        return {**resolution, "state": "elapsed", "reason": "window_elapsed"}
    resolution["slot_step_minutes"] = STEP_MINUTES
    return resolution


async def submit(user_id, body):
    raw = body.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    async with get_session_factory()() as session:
        user = await service.account(session, user_id, lock=True)
        pref = await session.get(CalendarPreference, user_id, with_for_update=True)
        service.check_pref(pref, body.expected_preferences_version, user.google_account_version)
        existing = await session.scalar(
            select(CalendarSlotRequest).where(
                CalendarSlotRequest.user_id == user_id,
                CalendarSlotRequest.request_id == body.request_id,
            )
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if existing:
            if existing.request_hash != digest:
                raise ApiError(
                    409, "idempotency_conflict", "Request ID already used for other input."
                )
            await check_current(session, user_id, existing)
            return view(existing)
        anchor = now
        parent_id = str(body.anchor_from_request_id) if body.anchor_from_request_id else None
        if parent_id:
            parent = await session.scalar(
                select(CalendarSlotRequest).where(
                    CalendarSlotRequest.id == parent_id, CalendarSlotRequest.user_id == user_id
                )
            )
            if parent is None:
                raise ApiError(404, "calendar_slot_request_missing", "Anchor request not found.")
            anchor = parent.anchor_at
        preferences = Preferences.model_validate(pref.preferences)
        try:
            valid_zone(preferences.timezone)
        except ValueError:
            raise ApiError(
                409, "calendar_timezone_invalid", "Save a named IANA timezone first."
            ) from None
        resolution = prepare(body, preferences, anchor, now)
        state = {"needs_clarification": "needs_clarification", "elapsed": "complete"}.get(
            resolution["state"], "processing"
        )
        row = CalendarSlotRequest(
            id=str(uuid4()),
            user_id=user_id,
            request_id=body.request_id,
            request_hash=digest,
            request=raw,
            anchor_at=anchor,
            anchor_from_request_id=parent_id,
            preferences_version=pref.version,
            account_version=user.google_account_version,
            policy_version=SLOT_POLICY,
            preferences=pref.preferences,
            state=state,
            resolution=resolution,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            result={"slots": [], "reason": resolution.get("reason")}
            if state != "processing"
            else None,
        )
        session.add(row)
        await session.flush()
        receipt = view(row)
        await session.commit()
    if state != "processing":
        return receipt
    # A committed processing receipt prevents duplicate/retried HTTP inference/reads.
    # Process loss leaves an expiring receipt; a new request may retain its original anchor.
    try:
        evidence = await service.query_freebusy(
            user_id,
            FreeBusyRequest(
                expected_preferences_version=body.expected_preferences_version,
                start=resolution["evidence_start"],
                end=resolution["evidence_end"],
            ),
        )
        return await finish(user_id, row.id, evidence.id)
    except ApiError as error:
        async with get_session_factory()() as session:
            current = await session.scalar(
                select(CalendarSlotRequest)
                .where(CalendarSlotRequest.id == row.id, CalendarSlotRequest.user_id == user_id)
                .with_for_update()
            )
            if current and current.state == "processing":
                current.state, current.error_code = "failed", error.code
                await session.commit()
        # Preserve error status/envelope: a stale/denied read is not a successful offer.
        raise ApiError(
            error.status, error.code, error.message, {"slot_request_id": row.id}
        ) from None


async def finish(user_id, request_id, evidence_id):
    async with get_session_factory()() as session:
        # All mutating paths lock account -> preferences -> request.
        await service.account(session, user_id, lock=True)
        await session.get(CalendarPreference, user_id, with_for_update=True)
        row = await session.scalar(
            select(CalendarSlotRequest)
            .where(CalendarSlotRequest.id == request_id, CalendarSlotRequest.user_id == user_id)
            .with_for_update()
        )
        if row is None:
            raise ApiError(404, "calendar_slot_request_missing", "Slot request not found.")
        now = await session.scalar(select(func.clock_timestamp()))
        await check_current(session, user_id, row)
        if row.state != "processing":
            return view(row)
        stored = await session.scalar(
            select(CalendarEvidence).where(
                CalendarEvidence.id == evidence_id, CalendarEvidence.user_id == user_id
            )
        )
        if stored is None or stored.expires_at <= now:
            raise expired()
        if (
            stored.policy_version != service.POLICY_VERSION
            or stored.preferences_version != row.preferences_version
            or stored.account_version != row.account_version
        ):
            raise service.conflict()
        evidence = FreeBusyOut.model_validate(stored.result)
        body = SlotRequest.model_validate(row.request)
        resolution = row.resolution
        if (
            evidence.start > parse_instant(resolution["evidence_start"])
            or evidence.end < parse_instant(resolution["evidence_end"])
            or set(item.calendar_id for item in evidence.calendars)
            != set(row.preferences["calendar_ids"])
        ):
            raise ApiError(
                409, "calendar_evidence_incomplete", "Calendar query does not cover the request."
            )
        row.evidence_id, row.calculated_at = stored.id, now
        row.expires_at = min(row.expires_at, stored.expires_at)
        if evidence.coverage != "complete" or any(c.status != "known" for c in evidence.calendars):
            row.state, row.result = "unknown", {"slots": [], "reason": "calendar_coverage_unknown"}
        else:
            prefs = Preferences.model_validate(row.preferences)
            duration = resolution["duration_minutes"]
            starts = availability.feasible_starts(
                parse_instant(resolution["search_start"]),
                parse_instant(resolution["end"]),
                duration,
                prefs,
                evidence.calendars,
                now,
                exact=resolution["exact"],
            )
            requested_count = 1 if resolution["exact"] else body.count
            chosen = availability.select_options(starts, duration, requested_count)
            options = [
                option(row.id, start, duration, resolution["timezone"], body.participant_timezones)
                for start in chosen
            ]
            row.state = "complete"
            row.result = {
                "slots": [item.model_dump(mode="json") for item in options],
                "reason": "insufficient_slots" if len(options) < requested_count else None,
            }
            if chosen:
                row.expires_at = min(
                    row.expires_at, chosen[0] - timedelta(minutes=prefs.minimum_notice_minutes)
                )
                if row.expires_at <= now:
                    raise expired()
        result = view(row)
        await session.commit()
        return result


def option(request_id, start, duration, zone_name, participants):
    end = start + timedelta(minutes=duration)
    zone = ZoneInfo(zone_name)
    return SlotOption(
        id=str(uuid5(UUID(request_id), start.isoformat() + "/" + end.isoformat())),
        start=start,
        end=end,
        timezone=zone_name,
        start_local=start.astimezone(zone).isoformat(),
        end_local=end.astimezone(zone).isoformat(),
        participant_displays=[
            {
                "timezone": name,
                "start_local": start.astimezone(ZoneInfo(name)).isoformat(),
                "end_local": end.astimezone(ZoneInfo(name)).isoformat(),
            }
            for name in participants
        ],
    )
