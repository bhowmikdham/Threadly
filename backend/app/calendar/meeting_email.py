"""Fresh email references and editable, explicitly confirmed Calendar previews.

Source text is displayed as data. Only the user's typed form supplies event details;
this path never calls a model or reads chat-level Always permission.
"""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.actions import calendar_preview
from app.actions import service as actions
from app.api.errors import ApiError
from app.assistant import source_data
from app.assistant.summary import digest
from app.calendar import service
from app.calendar.time_resolution import wall_instants
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import (
    ArtifactRevision,
    AssistantAction,
    AssistantTask,
    CalendarPreference,
    ContextSnapshot,
    TaskEvent,
)

POLICY = "meeting-email-calendar-1.0.0"


def require_live_mail():
    if get_settings().gmail_source_mode != "on_demand":
        raise ApiError(
            409, "live_mail_required", "This event editor requires on-demand Gmail reads."
        )


async def preferences(session, owner, expected=None):
    user = await calendar_preview.ready_account(session, owner)
    pref = await session.get(CalendarPreference, owner, populate_existing=True)
    service.check_pref(
        pref,
        expected if expected is not None else (pref.version if pref else 0),
        user.google_account_version,
    )
    return user, pref


def eligible(calendars, pref):
    return [
        {"id": item["id"], "name": item["summary"]}
        for item in calendars["calendars"]
        if item["event_write_acl"] and item["id"] in pref.preferences["calendar_ids"]
    ]


async def prepare(owner, request):
    require_live_mail()
    factory = get_session_factory()
    async with factory() as session:
        await preferences(session, owner)
    # All provider calls finish before source capture acquires account/thread locks.
    source = await source_data.fetch(owner, request.source.thread_id)
    if source["owner"] != owner:
        raise ApiError(404, "gmail_source_missing", "That email is not available.")
    message = next(
        (m for m in source["messages"] if m["gmail_msg_id"] == request.source.message_id), None
    )
    if message is None:
        raise ApiError(404, "gmail_source_missing", "That email is not in this thread.")
    calendars = await service.list_calendars(owner)
    async with factory.begin() as session:
        user, pref = await preferences(session, owner)
        if calendars["account_version"] != user.google_account_version:
            raise service.conflict()
        choices = eligible(calendars, pref)
        if not choices:
            raise ApiError(409, "calendar_write_acl_missing", "Select an editable calendar.")
        capture = await source_data.capture(
            session, owner, request.source.thread_id, message_id=request.source.message_id
        )
        return {
            "source": request.source.model_dump(),
            "context_snapshot_id": capture.id,
            "source_subject": message["subject"] or "",
            "source_sender": message["from_addr"],
            "source_excerpt": message["body_clean"][:6000],
            "source_truncated": len(message["body_clean"]) > 6000,
            "title": (message["subject"] or "")[:300],
            "timezone": pref.preferences["timezone"],
            "default_duration_minutes": pref.preferences["default_duration_minutes"],
            "preferences_version": pref.version,
            "calendars": choices,
            "confirmation_required": True,
        }


async def owned_source(session, owner, context_id, source):
    capture = await session.scalar(
        select(ContextSnapshot).where(
            ContextSnapshot.id == context_id, ContextSnapshot.user_id == owner
        )
    )
    if capture is None:
        raise ApiError(404, "context_not_found", "Unknown email capture.")
    ref = capture.payload
    ui = ref.get("ui_map") or {}
    if (
        ref.get("storage") != source_data.STORAGE
        or ref.get("thread_id") != source["thread_id"]
        or ui.get("selected_message_ids") != [source["message_id"]]
        or ui.get("visible_message_ids") != [source["message_id"]]
    ):
        raise ApiError(409, "meeting_email_source_changed", "Open the event editor again.")
    data = source_data.context_data(capture)
    await source_data.validate(session, owner, data)
    return capture


def times(request, pref):
    zone = ZoneInfo(pref.preferences["timezone"])
    starts = wall_instants(
        request.date, request.start_time.hour * 60 + request.start_time.minute, zone
    )
    if len(starts) != 1:
        raise ApiError(
            422,
            "calendar_time_ambiguous",
            "Choose another time; this local time is ambiguous or does not exist.",
        )
    start = starts[0]
    end = start + timedelta(minutes=request.duration_minutes)
    now = datetime.now(UTC)
    if start <= now or end > now + timedelta(days=90):
        raise ApiError(422, "calendar_window_invalid", "Choose a future time within 90 days.")
    return start, end


async def preview(owner, request):
    require_live_mail()
    factory = get_session_factory()
    source = request.source.model_dump()
    context_id = str(request.context_snapshot_id)
    key = str(uuid5(NAMESPACE_URL, f"{POLICY}:{owner}:{request.request_id}"))
    request_hash = digest(request.model_dump(mode="json"))
    async with factory() as session:
        await owned_source(session, owner, context_id, source)
        _, pref = await preferences(session, owner, request.expected_preferences_version)
        times(request, pref)
    calendars = await service.list_calendars(owner)
    async with factory.begin() as session:
        # Serialize duplicate editor submissions without holding any provider transaction.
        user = await calendar_preview.ready_account(session, owner, lock=True)
        previous = await session.scalar(
            select(AssistantAction).where(
                AssistantAction.user_id == owner, AssistantAction.proposal_request_id == key
            )
        )
        if previous:
            if previous.source_versions.get("request_hash") != request_hash:
                raise ApiError(409, "idempotency_conflict", "Preview key already used.")
            return await calendar_preview.view(session, owner, previous.id)
        capture = await owned_source(session, owner, context_id, source)
        now = await session.scalar(select(func.clock_timestamp()))
        if capture.created_at < now - timedelta(minutes=15):
            raise ApiError(
                409, "meeting_email_capture_expired", "Open this email's event editor again."
            )
        _, pref = await preferences(session, owner, request.expected_preferences_version)
        if calendars["account_version"] != user.google_account_version:
            raise service.conflict()
        calendar = next(
            (c for c in eligible(calendars, pref) if c["id"] == request.calendar_id), None
        )
        if calendar is None:
            raise ApiError(422, "calendar_not_selected", "Choose an editable scheduling calendar.")
        start, end = times(request, pref)
        event = {
            "id": uuid5(NAMESPACE_URL, key).hex,
            "summary": request.title,
            "description": request.description,
            "location": request.location,
            "start": {"dateTime": start.isoformat(), "timeZone": pref.preferences["timezone"]},
            "end": {"dateTime": end.isoformat(), "timeZone": pref.preferences["timezone"]},
            "attendees": [{"email": address} for address in request.attendees],
            "extendedProperties": {
                "private": {"threadlyAction": digest({"owner": owner, "key": key})}
            },
            "reminders": {"useDefault": False},
            "guestsCanModify": False,
            "guestsCanInviteOthers": False,
            "guestsCanSeeOtherGuests": True,
        }
        payload = {
            "calendar_id": calendar["id"],
            "calendar_name": calendar["name"],
            "send_updates": request.send_updates,
            "event": event,
        }
        task = AssistantTask(
            id=str(uuid5(NAMESPACE_URL, key + ":task")),
            user_id=owner,
            request_id=key,
            request_hash=request_hash,
            instruction="Prepare an event from the selected email for explicit review.",
            intent_hint="plan_schedule",
            state="succeeded",
            version=1,
            latest_sequence=1,
            context_snapshot_id=context_id,
            release={"workflow": POLICY},
        )
        session.add(task)
        await session.flush()
        artifact = ArtifactRevision(
            id=str(uuid5(NAMESPACE_URL, key + ":artifact")),
            task_id=task.id,
            user_id=owner,
            revision=1,
            payload={"kind": "calendar_event", "content": payload},
            provenance={"policy": POLICY, "source": source},
            draft_envelope=None,
        )
        session.add(artifact)
        await session.flush()
        task.final_artifact_id = artifact.id
        session.add(
            TaskEvent(
                task_id=task.id,
                user_id=owner,
                sequence=1,
                task_version=1,
                kind="task.succeeded",
                payload={"artifact_id": artifact.id},
            )
        )
        action = await actions.propose(
            session,
            owner,
            artifact.id,
            request_id=key,
            expected_revision=1,
            action_type="create_event",
            payload_schema=calendar_preview.SCHEMA,
            payload=payload,
            source_versions={
                "meeting_email": True,
                "source": source,
                "context_snapshot_id": context_id,
                "request_hash": request_hash,
                "account_version": user.google_account_version,
                "google_subject": user.google_sub,
                "preferences_version": pref.version,
                "session_version": user.threadly_session_version,
                "approval_mode": "ask",
            },
            expires_at=min(now + timedelta(minutes=10), start),
        )
        # Creating a candidate never approves or queues it, including in Always mode.
        return await calendar_preview.view(session, owner, action.id)


async def blockers(session, owner, action):
    expected = action.source_versions
    user = await calendar_preview.ready_account(session, owner)
    pref = await session.get(CalendarPreference, owner, populate_existing=True)
    result = []
    if (
        user.google_account_version != expected["account_version"]
        or user.google_sub != expected["google_subject"]
        or user.threadly_session_version != expected["session_version"]
    ):
        result.append("calendar_account_changed")
    if (
        pref is None
        or pref.version != expected["preferences_version"]
        or action.payload["calendar_id"] not in pref.preferences["calendar_ids"]
    ):
        result.append("calendar_preferences_changed")
    await owned_source(session, owner, expected["context_snapshot_id"], expected["source"])
    artifact = await session.get(ArtifactRevision, action.artifact_id)
    if (
        artifact is None
        or digest({"artifact": artifact.payload, "envelope": artifact.draft_envelope})
        != action.source_artifact_hash
    ):
        result.append("booking_artifact_changed")
    return result
