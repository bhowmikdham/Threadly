"""Short owned transactions around Google reads, fenced by account and preference versions."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.auth.service import get_valid_access_token
from app.calendar import client
from app.capabilities.service import build_capabilities
from app.db.engine import get_session_factory
from app.db.models import CalendarEvidence, CalendarPreference, User
from app.schemas.calendar import POLICY_VERSION, CalendarCoverage, FreeBusyOut, PreferencesOut


def conflict():
    return ApiError(
        409, "calendar_context_changed", "Calendar settings or connection changed; retry."
    )


def require_read(user):
    if user is None:
        raise ApiError(401, "unauthorized", "Unknown user.")
    capabilities = {item["id"]: item for item in build_capabilities(user)["capabilities"]}
    for key in ("calendar_list", "calendar_read"):
        if not capabilities[key]["ready"]:
            raise ApiError(
                403, "calendar_connection_required", "Connect Calendar read access first."
            )


async def account(session, user_id, *, lock=False, expected=None):
    user = await session.get(User, user_id, with_for_update=lock, populate_existing=True)
    require_read(user)
    if expected is not None and expected != user.google_account_version:
        raise conflict()
    return user


def pref_view(row):
    return PreferencesOut(
        version=row.version,
        account_version=row.account_version,
        policy_version=row.policy_version,
        preferences=row.preferences,
    )


async def get_preferences(user_id):
    async with get_session_factory()() as session:
        if await session.get(User, user_id) is None:
            raise ApiError(401, "unauthorized", "Unknown user.")
        row = await session.get(CalendarPreference, user_id)
        if row is None:
            raise ApiError(
                404, "calendar_preferences_missing", "Save scheduling preferences first."
            )
        return pref_view(row)


async def _snapshot(user_id):
    async with get_session_factory()() as session:
        user = await account(session, user_id)
        version = user.google_account_version
    # The helper owns its token transactions and makes refresh calls outside them.
    async with get_session_factory()() as token_session:
        token = await get_valid_access_token(token_session, user)
    async with get_session_factory()() as session:
        await account(session, user_id, expected=version)
    return token, version


async def list_calendars(user_id):
    token, version = await _snapshot(user_id)
    items = await client.list_calendars(token)
    async with get_session_factory()() as session:
        await account(session, user_id, lock=True, expected=version)
        now = await session.scalar(select(func.clock_timestamp()))
        return {"account_version": version, "checked_at": now, "calendars": items}


async def save_preferences(user_id, body):
    token, version = await _snapshot(user_id)
    # Fail known stale updates before spending on a provider read; recheck under lock afterwards.
    async with get_session_factory()() as session:
        row = await session.get(CalendarPreference, user_id)
        if (row.version if row else 0) != body.expected_version:
            raise conflict()
    calendars = await client.list_calendars(token)
    accessible = {item["id"] for item in calendars if item["can_read_busy"]}
    if not set(body.preferences.calendar_ids) <= accessible:
        raise ApiError(422, "calendar_not_selectable", "Select readable calendars from your list.")
    async with get_session_factory()() as session:
        await account(session, user_id, lock=True, expected=version)
        row = await session.get(CalendarPreference, user_id, with_for_update=True)
        if (row.version if row else 0) != body.expected_version:
            raise conflict()
        if row is None:
            row = CalendarPreference(user_id=user_id, version=0)
            session.add(row)
        row.version += 1
        row.account_version = version
        row.policy_version = POLICY_VERSION
        row.preferences = body.preferences.model_dump(mode="json")
        await session.flush()
        result = pref_view(row)
        await session.commit()
        return result


def check_pref(row, expected, account_version):
    if row is None:
        raise ApiError(404, "calendar_preferences_missing", "Save scheduling preferences first.")
    if (
        row.version != expected
        or row.account_version != account_version
        or row.policy_version != POLICY_VERSION
    ):
        raise conflict()


async def query_freebusy(user_id, body):
    token, version = await _snapshot(user_id)
    async with get_session_factory()() as session:
        row = await session.get(CalendarPreference, user_id)
        check_pref(row, body.expected_preferences_version, version)
        ids = list(row.preferences["calendar_ids"])
        checked = await session.scalar(select(func.clock_timestamp()))
        if body.start < checked - timedelta(minutes=5) or body.end > checked + timedelta(days=90):
            raise ApiError(
                422, "calendar_window_invalid", "Choose a window within the next 90 days."
            )
    # Re-read current ACLs. A disappeared calendar is unknown, never silently omitted.
    calendars = await client.list_calendars(token)
    accessible = {item["id"] for item in calendars if item["can_read_busy"]}
    queried = [cid for cid in ids if cid in accessible]
    results = await client.freebusy(token, queried, body.start, body.end) if queried else []
    by_id = {item.calendar_id: item for item in results}
    results = [
        by_id.get(cid)
        or CalendarCoverage(calendar_id=cid, status="unknown", reason="not_accessible", busy=[])
        for cid in ids
    ]
    expires = checked + timedelta(minutes=5)
    async with get_session_factory()() as session:
        await account(session, user_id, lock=True, expected=version)
        row = await session.get(CalendarPreference, user_id, with_for_update=True)
        check_pref(row, body.expected_preferences_version, version)
        now = await session.scalar(select(func.clock_timestamp()))
        if now >= expires:
            raise ApiError(409, "calendar_evidence_expired", "Calendar check expired; retry.")
        result = FreeBusyOut(
            id=str(uuid4()),
            preferences_version=row.version,
            account_version=version,
            policy_version=POLICY_VERSION,
            checked_at=checked,
            expires_at=expires,
            start=body.start,
            end=body.end,
            calendars=results,
            coverage="complete" if all(item.status == "known" for item in results) else "unknown",
        )
        session.add(
            CalendarEvidence(
                id=result.id,
                user_id=user_id,
                preferences_version=row.version,
                account_version=version,
                policy_version=POLICY_VERSION,
                checked_at=checked,
                expires_at=expires,
                result=result.model_dump(mode="json"),
            )
        )
        await session.commit()
        return result


async def get_evidence(user_id, evidence_id):
    async with get_session_factory()() as session:
        user = await account(session, user_id, lock=True)
        row = await session.scalar(
            select(CalendarEvidence).where(
                CalendarEvidence.id == evidence_id, CalendarEvidence.user_id == user_id
            )
        )
        if row is None:
            raise ApiError(404, "calendar_evidence_missing", "Calendar evidence not found.")
        pref = await session.get(CalendarPreference, user_id, with_for_update=True)
        check_pref(pref, row.preferences_version, user.google_account_version)
        if (
            row.account_version != user.google_account_version
            or row.policy_version != POLICY_VERSION
        ):
            raise conflict()
        if row.expires_at <= await session.scalar(select(func.clock_timestamp())):
            raise ApiError(409, "calendar_evidence_expired", "Calendar check expired; retry.")
        return FreeBusyOut.model_validate(row.result)
