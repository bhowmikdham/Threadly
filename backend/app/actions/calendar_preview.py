"""Exact single-event approval. Email approval cannot authorize this schema."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.actions import approval, service
from app.api.errors import ApiError
from app.assistant import tasks
from app.assistant.summary import digest
from app.calendar import negotiations
from app.calendar import service as calendars
from app.capabilities.service import build_capabilities
from app.db.models import (
    ActionApproval,
    ActionJob,
    ArtifactRevision,
    AssistantAction,
    CalendarPreference,
    CalendarSlotRequest,
    MeetingNegotiation,
    MeetingSelection,
    Thread,
    User,
)

SCHEMA = "calendar-event-1.0.0"


async def selection_source(session, owner, selection_id):
    selection = await negotiations.owned(session, MeetingSelection, owner, selection_id)
    neg = await negotiations.owned(session, MeetingNegotiation, owner, selection.negotiation_id)
    thread = await session.get(Thread, neg.thread_id, populate_existing=True)
    if thread is None or thread.user_id != owner:
        raise ApiError(409, "booking_source_changed", "Recapture the meeting thread.")
    # Callers hold account lock before this check when mutating. Reads may acquire
    # account/preferences here, but no Calendar operation waits for an action task.
    selected = await negotiations.selection_view(session, neg, thread, selection)
    if not selected.usable:
        raise ApiError(
            409,
            "booking_selection_changed",
            "Recheck the selected meeting time.",
            {"blockers": selected.blockers},
        )
    offer = await negotiations.owned(session, negotiations.MeetingOffer, owner, selection.offer_id)
    query = await session.get(CalendarSlotRequest, offer.slot_request_id)
    return selection, neg, thread, selected, query


async def ready_account(session, owner, *, lock=False):
    user = await calendars.account(session, owner, lock=lock)
    capability = next(
        c for c in build_capabilities(user)["capabilities"] if c["id"] == "calendar_write"
    )
    if capability["scope_status"] != "granted":
        raise ApiError(
            409, "calendar_write_scope_required", "Reconnect with Calendar event permission."
        )
    return user


async def propose(session, owner, artifact_id, request):
    request_hash = digest({"artifact_id": artifact_id, "request": request.model_dump()})
    old = await session.scalar(
        select(AssistantAction).where(
            AssistantAction.user_id == owner,
            AssistantAction.proposal_request_id == request.request_id,
        )
    )
    if old:
        if old.payload_schema != SCHEMA or old.payload.get("request_hash") != request_hash:
            raise ApiError(409, "idempotency_conflict", "Proposal key already used.")
        return old
    artifact = await session.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.id == artifact_id, ArtifactRevision.user_id == owner
        )
    )
    if artifact is None:
        raise ApiError(404, "not_found", "Unknown scheduling artifact.")
    await tasks.owned_task(session, owner, artifact.task_id, lock=True)
    user = await ready_account(session, owner, lock=True)
    selection, neg, thread, selected, query = await selection_source(
        session, owner, request.selection_id
    )
    if (
        artifact.payload.get("kind") not in {"schedule_options", "availability"}
        or artifact.payload.get("content", {}).get("slot_request_id") != query.id
    ):
        raise ApiError(
            409,
            "booking_artifact_mismatch",
            "Use the scheduling artifact which supplied this offer.",
        )
    pref = await session.get(CalendarPreference, owner)
    if request.calendar_id not in pref.preferences["calendar_ids"]:
        raise ApiError(
            422, "calendar_not_selected", "Select a destination from your scheduling calendars."
        )
    slot = selected.slot.model_dump(mode="json")
    event_id = uuid5(NAMESPACE_URL, f"threadly:event:{owner}:{selection.id}").hex
    marker = digest({"owner": owner, "request_hash": request_hash, "selection_id": selection.id})
    payload = {
        "request_hash": request_hash,
        "calendar_id": request.calendar_id,
        "send_updates": request.send_updates,
        "event": {
            "id": event_id,
            "summary": request.title,
            "description": request.description,
            "location": request.location,
            "start": {"dateTime": slot["start"], "timeZone": slot["timezone"]},
            "end": {"dateTime": slot["end"], "timeZone": slot["timezone"]},
            "attendees": [{"email": address} for address in request.attendees],
            "extendedProperties": {"private": {"threadlyAction": marker}},
            "reminders": {"useDefault": False},
            "guestsCanModify": False,
            "guestsCanInviteOthers": False,
            "guestsCanSeeOtherGuests": True,
        },
    }
    sources = {
        "selection_id": selection.id,
        "negotiation_id": neg.id,
        "negotiation_version": neg.version,
        "thread_version": thread.version,
        "account_version": user.google_account_version,
        "google_subject": user.google_sub,
        "preferences_version": pref.version,
        "slot_request_id": query.id,
        "slot_id": selection.slot_id,
    }
    now = await session.scalar(select(func.clock_timestamp()))
    return await service.propose(
        session,
        owner,
        artifact_id,
        request_id=request.request_id,
        expected_revision=request.expected_revision,
        action_type="create_event",
        payload_schema=SCHEMA,
        payload=payload,
        source_versions=sources,
        expires_at=min(selection.expires_at, query.expires_at, now + timedelta(minutes=10)),
    )


async def blockers(session, owner, action):
    result = []
    if action.payload_schema != SCHEMA or action.action_type != "create_event":
        raise ApiError(404, "not_found", "Unknown Calendar action.")
    if service.candidate_hash(SCHEMA, action.payload) != action.payload_hash:
        result.append("payload_integrity_failed")
    if action.expires_at <= await session.scalar(select(func.clock_timestamp())):
        result.append("action_expired")
    try:
        user = await ready_account(session, owner)
        selection, neg, thread, selected, query = await selection_source(
            session, owner, action.source_versions["selection_id"]
        )
        expected = action.source_versions
        pref = await session.get(CalendarPreference, owner, populate_existing=True)
        if (
            user.google_account_version != expected["account_version"]
            or user.google_sub != expected["google_subject"]
            or pref.version != expected["preferences_version"]
            or neg.version != expected["negotiation_version"]
            or thread.version != expected["thread_version"]
            or selection.slot_id != expected["slot_id"]
        ):
            result.append("booking_source_changed")
        artifact = await session.get(ArtifactRevision, action.artifact_id)
        if (
            artifact is None
            or digest({"artifact": artifact.payload, "envelope": artifact.draft_envelope})
            != action.source_artifact_hash
        ):
            result.append("booking_artifact_changed")
    except ApiError as error:
        result.append(error.code)
    return result


async def view(session, owner, action_id):
    from app.actions.calendar_executor import enabled

    _, action = await service.owned_action(session, owner, action_id)
    reasons = await blockers(session, owner, action)
    return {
        "action_id": action.id,
        "task_id": action.task_id,
        "artifact_id": action.artifact_id,
        "action_type": "create_event",
        "state": action.state,
        "version": action.version,
        "payload_hash": action.payload_hash,
        "payload_schema": action.payload_schema,
        "expires_at": action.expires_at.isoformat(),
        "preview": action.payload,
        "blockers": reasons,
        "approval_available": action.state == "proposed" and not reasons and enabled(owner),
        "result": action.result,
        "error_code": action.error_code,
        "authorization": "separate_exact_event_approval",
        "atomic_with_email": False,
    }


async def approve(session, owner, action_id, request, *, transport=None):
    try:
        return await _approve(session, owner, action_id, request, transport=transport)
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23514" and any(
            marker in str(error.orig)
            for marker in (
                "Matching unexpired approval required",
                "Approval must bind current unexpired action",
            )
        ):
            raise ApiError(
                409,
                "calendar_approval_blocked",
                "Refresh the event preview.",
                {"blockers": ["action_expired"]},
            ) from None
        raise


async def _approve(session, owner, action_id, request, *, transport=None):
    from app.actions.calendar_executor import enabled

    task, action = await service.owned_action(session, owner, action_id, lock=True)
    if action.payload_schema != SCHEMA or action.action_type != "create_event":
        raise ApiError(409, "action_execution_unavailable", "This is not a Calendar event action.")
    hashed = approval.request_hash(action_id, "approve", request)
    old = await session.scalar(
        select(ActionApproval).where(
            ActionApproval.user_id == owner, ActionApproval.request_id == request.request_id
        )
    )
    if old:
        approval.check_replay(old, hashed)
        return action
    if (
        action.state != "proposed"
        or action.version != request.expected_version
        or action.payload_hash != request.payload_hash
    ):
        raise ApiError(409, "action_changed", "Review the exact current event preview.")
    await session.get(User, owner, with_for_update=True, populate_existing=True)
    reasons = await blockers(session, owner, action)
    if reasons or not enabled(owner, transport):
        raise ApiError(
            409,
            "calendar_approval_blocked",
            "Resolve event preview blockers before approval.",
            {
                "blockers": reasons
                + ([] if enabled(owner, transport) else ["calendar_writes_disabled"])
            },
        )
    approval_id = str(uuid4())
    inserted = await session.scalar(
        insert(ActionApproval)
        .values(
            id=approval_id,
            user_id=owner,
            action_id=action.id,
            action_version=action.version,
            payload_hash=action.payload_hash,
            request_id=request.request_id,
            request_hash=hashed,
            expires_at=action.expires_at,
        )
        .on_conflict_do_nothing(constraint="uq_approval_request")
        .returning(ActionApproval.id)
    )
    if inserted is None:
        old = await session.scalar(
            select(ActionApproval).where(
                ActionApproval.user_id == owner, ActionApproval.request_id == request.request_id
            )
        )
        approval.check_replay(old, hashed)
        return action
    action.state, action.version = "approved", action.version + 1
    await session.flush()
    session.add(
        ActionJob(
            action_id=action.id,
            user_id=owner,
            approval_id=approval_id,
            kind="dispatch",
            state="queued",
        )
    )
    task.version += 1
    tasks.add_event(
        session, task, "action.state_changed", {"action_id": action.id, "state": "approved"}
    )
    await session.flush()
    return action
