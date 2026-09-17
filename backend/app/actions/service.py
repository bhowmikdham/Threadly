"""Internal action storage only. Caller owns transaction; no tokens, model or network calls.

A future payload builder supplies the exact candidate. This module neither builds
Gmail MIME nor grants approval, claims action jobs, dispatches or reconciles writes.
"""

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.actions.state import PRE_DISPATCH, allows
from app.api.errors import ApiError
from app.assistant import tasks
from app.assistant.summary import digest
from app.db.models import ActionJob, ArtifactRevision, AssistantAction
from app.schemas.actions import ActionCandidate

PAYLOAD_BYTES = 128_000


def candidate_hash(payload_schema, payload):
    return digest({"schema": payload_schema, "payload": payload})


async def owned_action(session, user_id: int, action_id: str, *, lock=False):
    # Read identity first, then acquire locks only in task -> action order.
    identity = (
        await session.execute(
            select(AssistantAction.task_id).where(
                AssistantAction.id == action_id, AssistantAction.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if identity is None:
        raise ApiError(404, "not_found", "Unknown assistant action.")
    task = await tasks.owned_task(session, user_id, identity, lock=lock)
    if lock:
        await session.refresh(task)
    query = select(AssistantAction).where(
        AssistantAction.id == action_id, AssistantAction.user_id == user_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    action = await session.scalar(query)
    if action is None:
        raise ApiError(404, "not_found", "Unknown assistant action.")
    return task, action


async def propose(
    session,
    user_id: int,
    artifact_id: str,
    *,
    request_id: str,
    expected_revision: int,
    action_type: str,
    payload_schema: str,
    payload: dict,
    source_versions: dict,
    expires_at: datetime,
):
    """Store a backend-built candidate. Never exposed as an arbitrary-payload HTTP API."""
    import json

    try:
        ActionCandidate.model_validate(
            {
                "request_id": request_id,
                "expected_revision": expected_revision,
                "action_type": action_type,
                "payload_schema": payload_schema,
                "payload": payload,
                "source_versions": source_versions,
                "expires_at": expires_at,
            }
        )
    except ValidationError:
        raise ApiError(422, "invalid_action_candidate", "Invalid action candidate.") from None
    try:
        encoded = json.dumps(
            {"payload": payload, "source_versions": source_versions}, allow_nan=False
        )
    except (ValueError, TypeError):
        raise ApiError(422, "invalid_action_candidate", "Invalid action candidate.") from None
    if len(encoded.encode()) > PAYLOAD_BYTES:
        raise ApiError(422, "action_payload_too_large", "Action candidate exceeds its size limit.")
    expires_at = expires_at.astimezone(UTC)
    hashed = digest(
        {
            "artifact_id": artifact_id,
            "expected_revision": expected_revision,
            "action_type": action_type,
            "payload_schema": payload_schema,
            "payload": payload,
            "source_versions": source_versions,
            "expires_at": expires_at.isoformat(),
        }
    )
    previous = await session.scalar(
        select(AssistantAction).where(
            AssistantAction.user_id == user_id, AssistantAction.proposal_request_id == request_id
        )
    )
    if previous:
        if previous.proposal_hash != hashed:
            raise ApiError(
                409, "idempotency_conflict", "Proposal key was used for different input."
            )
        return previous
    artifact = await session.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.id == artifact_id, ArtifactRevision.user_id == user_id
        )
    )
    if artifact is None:
        raise ApiError(404, "not_found", "Unknown artifact.")
    task = await tasks.owned_task(session, user_id, artifact.task_id, lock=True)
    await session.refresh(task)
    # A competing acceptance may have committed while this task lock was awaited.
    previous = await session.scalar(
        select(AssistantAction).where(
            AssistantAction.user_id == user_id, AssistantAction.proposal_request_id == request_id
        )
    )
    if previous:
        if previous.proposal_hash != hashed:
            raise ApiError(
                409, "idempotency_conflict", "Proposal key was used for different input."
            )
        return previous
    calendar_intermediate = False
    if action_type == "create_event" and task.release.get("workflow") == "mvp-workflow-1.0.0":
        from app.db.models import AssistantStep

        calendar_intermediate = (
            await session.scalar(
                select(AssistantStep.artifact_id).where(
                    AssistantStep.task_id == task.id,
                    AssistantStep.user_id == user_id,
                    AssistantStep.operation == "schedule",
                    AssistantStep.state == "succeeded",
                    AssistantStep.artifact_id == artifact.id,
                )
            )
            is not None
        )
    if (
        task.state != "succeeded"
        or (task.final_artifact_id != artifact.id and not calendar_intermediate)
        or artifact.revision != expected_revision
    ):
        raise ApiError(409, "revision_conflict", "Use the current completed artifact.")
    kinds = {"draft"} if action_type == "send_email" else {"schedule_options", "availability"}
    if artifact.payload.get("kind") not in kinds:
        raise ApiError(409, "action_artifact_mismatch", "Artifact cannot support this action type.")
    now = await session.scalar(select(func.clock_timestamp()))
    if not now < expires_at <= now + timedelta(hours=24):
        raise ApiError(422, "action_expiry_invalid", "Choose an expiry within the next 24 hours.")
    artifact_hash = digest({"artifact": artifact.payload, "envelope": artifact.draft_envelope})
    action_id = str(uuid4())
    inserted = await session.scalar(
        insert(AssistantAction)
        .values(
            id=action_id,
            user_id=user_id,
            task_id=task.id,
            artifact_id=artifact.id,
            action_type=action_type,
            proposal_request_id=request_id,
            proposal_hash=hashed,
            payload_schema=payload_schema,
            payload=copy.deepcopy(payload),
            payload_hash=candidate_hash(payload_schema, payload),
            source_artifact_hash=artifact_hash,
            source_versions=copy.deepcopy(source_versions),
            expires_at=expires_at,
            state="proposed",
            version=1,
        )
        .on_conflict_do_nothing(constraint="uq_action_proposal_request")
        .returning(AssistantAction.id)
    )
    if inserted is None:
        previous = await session.scalar(
            select(AssistantAction).where(
                AssistantAction.user_id == user_id,
                AssistantAction.proposal_request_id == request_id,
            )
        )
        if previous.proposal_hash != hashed:
            raise ApiError(
                409, "idempotency_conflict", "Proposal key was used for different input."
            )
        return previous
    task.version += 1
    tasks.add_event(
        session,
        task,
        "action.proposed",
        {
            "action_id": action_id,
            "artifact_id": artifact.id,
            "action_type": action_type,
            "state": "proposed",
        },
    )
    await session.flush()
    return await session.get(AssistantAction, action_id)


async def stop_before_dispatch(
    session, user_id: int, action_id: str, *, expected_version: int, state: str
):
    """Only non-executing terminal transitions are installed in this slice."""
    if state not in {"cancelled", "rejected", "expired", "superseded"}:
        raise ApiError(
            409, "action_execution_unavailable", "Approval and execution are not installed."
        )
    task, action = await owned_action(session, user_id, action_id, lock=True)
    if action.state == state and expected_version in {action.version, action.version - 1}:
        return action
    if action.version != expected_version:
        raise ApiError(409, "version_conflict", "Action changed; reload its current state.")
    if not allows(action.state, state):
        raise ApiError(409, "action_state_conflict", "Action cannot make that transition.")
    if state == "expired" and action.expires_at > await session.scalar(
        select(func.clock_timestamp())
    ):
        raise ApiError(409, "action_not_expired", "The action has not expired.")
    action.state, action.version = state, action.version + 1
    job = await session.get(ActionJob, action.id)
    if job:
        job.state, job.lease_token, job.lease_expires_at = "done", None, None
    task.version += 1
    tasks.add_event(session, task, "action.state_changed", {"action_id": action.id, "state": state})
    await session.flush()
    return action


async def supersede_for_edit(session, task, artifact_id: str):
    """Caller holds the task lock. Never imply an in-flight action was recalled."""
    actions = (
        await session.scalars(
            select(AssistantAction)
            .where(
                AssistantAction.task_id == task.id,
                AssistantAction.user_id == task.user_id,
                AssistantAction.artifact_id == artifact_id,
                AssistantAction.state.in_(PRE_DISPATCH),
            )
            .order_by(AssistantAction.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    for action in actions:
        action.state, action.version = "superseded", action.version + 1
        job = await session.get(ActionJob, action.id)
        if job:
            job.state, job.lease_token, job.lease_expires_at = "done", None, None
        tasks.add_event(
            session,
            task,
            "action.state_changed",
            {"action_id": action.id, "state": "superseded", "reason": "draft_revised"},
        )


async def source_deletion_blockers(session, user_id: int, task_id: str):
    """Internal preflight; database RESTRICT also prevents uncoordinated cascades."""
    await tasks.owned_task(session, user_id, task_id, lock=True)
    actions = (
        await session.scalars(
            select(AssistantAction)
            .where(AssistantAction.user_id == user_id, AssistantAction.task_id == task_id)
            .order_by(AssistantAction.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    return [
        {
            "action_id": a.id,
            "state": a.state,
            "reason": "unresolved_external_outcome"
            if a.state in {"executing", "outcome_unknown"}
            else "action_history_retention_pending",
        }
        for a in actions
    ]
