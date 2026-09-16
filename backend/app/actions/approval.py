"""Exact approval/outbox and durable stop decisions; caller owns the transaction.

No dispatcher is installed. The internal execution_enabled seam is exercised only
with fake dispatch in tests; HTTP never supplies or enables it in this release.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.actions import email_payload, email_preview, service
from app.api.errors import ApiError
from app.assistant import tasks
from app.assistant.summary import digest
from app.db.models import ActionApproval, ActionDecision, ActionJob, User


def request_hash(action_id, operation, request):
    return digest({"action_id": action_id, "operation": operation, **request.model_dump()})


def check_replay(row, hashed):
    if row.request_hash != hashed:
        raise ApiError(409, "idempotency_conflict", "Request key was used for different input.")


async def approve(session, user_id, action_id, request, *, execution_enabled=False):
    try:
        return await _approve(
            session, user_id, action_id, request, execution_enabled=execution_enabled
        )
    except IntegrityError as error:
        # Time can cross expiry between the last read and PostgreSQL's guard.
        # The caller must roll back this failed transaction, never retry the insert.
        if getattr(error.orig, "sqlstate", None) == "23514" and any(
            marker in str(error.orig)
            for marker in (
                "Matching unexpired approval required",
                "Approval must bind current unexpired action",
            )
        ):
            raise ApiError(
                409,
                "action_approval_blocked",
                "Refresh the action preview.",
                {"blockers": ["action_expired"]},
            ) from None
        raise


async def _approve(session, user_id, action_id, request, *, execution_enabled):
    task, action = await service.owned_action(session, user_id, action_id, lock=True)
    hashed = request_hash(action_id, "approve", request)
    old = await session.scalar(
        select(ActionApproval).where(
            ActionApproval.user_id == user_id, ActionApproval.request_id == request.request_id
        )
    )
    if old:
        check_replay(old, hashed)
        return {"request_id": request.request_id, "operation": "approve", "decision": "approved"}
    if action.version != request.expected_version:
        raise ApiError(409, "version_conflict", "Action changed; reload its current state.")
    if action.payload_hash != request.payload_hash:
        raise ApiError(409, "payload_conflict", "Approval must match the saved payload hash.")
    if action.state != "proposed":
        raise ApiError(409, "action_state_conflict", "Only a proposed action can be approved.")
    if action.payload_schema != email_payload.SCHEMA or action.action_type != "send_email":
        raise ApiError(409, "action_execution_unavailable", "No executor for this action schema.")
    # Sync and auth publication both hold the user lock. Neither acquires task/action
    # locks afterward. Hold it through validation/enqueue; never through provider I/O.
    await session.get(User, user_id, with_for_update=True, populate_existing=True)
    view = await email_preview.view(session, user_id, action_id)
    blockers = [b for b in view["blockers"] if b != "send_executor_unavailable"]
    if blockers:
        raise ApiError(
            409, "action_approval_blocked", "Refresh the action preview.", {"blockers": blockers}
        )
    if not execution_enabled:
        raise ApiError(409, "action_execution_unavailable", "Email approval is not enabled.")
    approval_id = str(uuid4())
    inserted = await session.scalar(
        insert(ActionApproval)
        .values(
            id=approval_id,
            user_id=user_id,
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
                ActionApproval.user_id == user_id, ActionApproval.request_id == request.request_id
            )
        )
        check_replay(old, hashed)
        return {"request_id": request.request_id, "operation": "approve", "decision": "approved"}
    action.state, action.version = "approved", action.version + 1
    await session.flush()  # Approval must exist before the database transition guard.
    session.add(
        ActionJob(
            action_id=action.id,
            user_id=user_id,
            approval_id=approval_id,
            kind="dispatch",
            state="queued",
        )
    )
    task.version += 1
    tasks.add_event(
        session, task, "action.state_changed", {"action_id": action.id, "state": "approved"}
    )
    await session.flush()  # Queue failure must roll back approval and state together.
    return {"request_id": request.request_id, "operation": "approve", "decision": "approved"}


async def stop(session, user_id, action_id, operation, request):
    if operation not in {"reject", "cancel"}:
        raise ValueError("Unsupported stop operation")
    task, action = await service.owned_action(session, user_id, action_id, lock=True)
    hashed = request_hash(action_id, operation, request)
    query = select(ActionDecision).where(
        ActionDecision.user_id == user_id,
        ActionDecision.operation == operation,
        ActionDecision.request_id == request.request_id,
    )
    old = await session.scalar(query)
    if old:
        check_replay(old, hashed)
        return {"request_id": request.request_id, "operation": operation, "decision": old.decision}
    if action.version != request.expected_version:
        raise ApiError(409, "version_conflict", "Action changed; reload its current state.")
    if operation == "reject" and action.state == "proposed":
        decision = "rejected"
    elif operation == "cancel" and action.state in {"proposed", "approved"}:
        decision = "cancelled"
    elif operation == "cancel" and action.state in {"executing", "outcome_unknown"}:
        decision = "cancellation_requested"
    else:
        raise ApiError(409, "action_state_conflict", "Action cannot accept this decision.")
    inserted = await session.scalar(
        insert(ActionDecision)
        .values(
            id=str(uuid4()),
            user_id=user_id,
            action_id=action.id,
            operation=operation,
            request_id=request.request_id,
            request_hash=hashed,
            expected_version=request.expected_version,
            decision=decision,
        )
        .on_conflict_do_nothing(constraint="uq_action_decision_request")
        .returning(ActionDecision.id)
    )
    if inserted is None:
        old = await session.scalar(query)
        check_replay(old, hashed)
        return {"request_id": request.request_id, "operation": operation, "decision": old.decision}
    if decision == "cancellation_requested":
        # Do not mark not-sent, recall, clear a lease, or suppress reconciliation.
        task.version += 1
        tasks.add_event(session, task, "action.cancellation_requested", {"action_id": action.id})
    else:
        await service.stop_before_dispatch(
            session, user_id, action.id, expected_version=request.expected_version, state=decision
        )
    await session.flush()
    return {"request_id": request.request_id, "operation": operation, "decision": decision}
