"""Read-only freshness checks safe inside the existing email task/account lock order."""

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.calendar import service
from app.db.models import (
    ArtifactRevision,
    CalendarEvidence,
    CalendarPreference,
    CalendarSlotRequest,
)


async def check_calendar(session, owner, payload):
    binding = payload.get("calendar_grounding")
    if binding is None:
        return None
    row = await session.scalar(
        select(CalendarSlotRequest).where(
            CalendarSlotRequest.id == binding["slot_request_id"],
            CalendarSlotRequest.user_id == owner,
        )
    )
    if row is None or row.state != "complete":
        raise ApiError(
            409, "calendar_grounding_changed", "Recheck availability and prepare a new draft."
        )
    user = await service.account(session, owner, expected=row.account_version)
    pref = await session.get(CalendarPreference, owner, populate_existing=True)
    service.check_pref(pref, row.preferences_version, user.google_account_version)
    now = await session.scalar(select(func.clock_timestamp()))
    evidence = await session.scalar(
        select(CalendarEvidence).where(
            CalendarEvidence.id == row.evidence_id, CalendarEvidence.user_id == owner
        )
    )
    if (
        pref.preferences != row.preferences
        or row.expires_at <= now
        or evidence is None
        or evidence.expires_at <= now
        or binding["expires_at"] != row.expires_at.isoformat()
    ):
        raise ApiError(
            409, "calendar_grounding_expired", "Recheck availability and prepare a new draft."
        )
    source = await session.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.id == binding["source_artifact_id"], ArtifactRevision.user_id == owner
        )
    )
    options = (row.result or {}).get("slots", [])
    if (
        source is None
        or source.payload.get("content", {}).get("slot_request_id") != row.id
        or binding["slot_ids"] != [s["id"] for s in options]
        or source.payload["content"].get("slots") != options
    ):
        raise ApiError(
            409, "calendar_grounding_changed", "Calendar options no longer match this draft."
        )
    return {
        "query_id": row.id,
        "source_artifact_id": source.id,
        "source_hash": digest(source.payload),
        "expires_at": binding["expires_at"],
        "account_version": row.account_version,
        "preferences_version": row.preferences_version,
    }


async def check_plan(session, owner, payload):
    from app.assistant import planning

    binding = payload.get("plan_grounding")
    if binding is None:
        return None
    plan = await planning.accepted_plan(session, owner, binding["artifact_id"])
    if (
        digest(plan.payload) != binding["payload_hash"]
        or plan.revision != binding["revision"]
        or plan.payload["content"]["accepted_item_ids"] != binding["accepted_item_ids"]
    ):
        raise ApiError(409, "accepted_plan_changed", "Regenerate the draft from the current plan.")
    return binding
