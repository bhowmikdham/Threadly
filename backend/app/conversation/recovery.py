"""Fence interrupted turns without discarding committed work or enabling retries twice."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.api.errors import ApiError
from app.conversation import store
from app.db.engine import get_session_factory
from app.db.models import (
    ArtifactRevision,
    AssistantAction,
    AssistantTask,
    CommandPlan,
    SchedulingProposal,
    TaskInput,
    User,
)


async def has_uncheckpointed_work(session, owner, conversation_id, request_id):
    key = str(uuid5(NAMESPACE_URL, f"calendar-event:{owner}:{conversation_id}:{request_id}"))
    keys = ["chat-" + request_id, key]
    for model, column in (
        (AssistantTask, AssistantTask.request_id),
        (CommandPlan, CommandPlan.request_id),
        (SchedulingProposal, SchedulingProposal.request_id),
        (TaskInput, TaskInput.request_id),
        (ArtifactRevision, ArtifactRevision.edit_request_id),
        (AssistantAction, AssistantAction.proposal_request_id),
    ):
        if await session.scalar(
            select(model.id).where(model.user_id == owner, column.in_(keys)).limit(1)
        ):
            return True
    return False


async def recover(owner, conversation_id, request, *, factory=None):
    factory = factory or get_session_factory()
    async with factory.begin() as session:
        # Same User -> Conversation fence as admission. A late in-flight mutation
        # must checkpoint with its old lease in the SAME transaction and will roll back.
        user = await session.get(User, owner, with_for_update=True)
        if user is None:
            raise ApiError(401, "unauthorized", "Sign in again.")
        row = await store.owned(session, owner, conversation_id, lock=True)
        state = store.decode(row)
        saved = next(
            (r for r in state.get("receipts", []) if r["request_id"] == request.pending_request_id),
            None,
        )
        if saved:
            result = {**saved["response"], "recovered_request_id": request.pending_request_id}
        else:
            if row.version != request.expected_version:
                raise ApiError(409, "conversation_version_conflict", "Reload this chat first.")
            if row.pending_request_id != request.pending_request_id or not row.pending_hash:
                raise ApiError(
                    409,
                    "conversation_pending_changed",
                    "The unfinished request changed. Reload this chat.",
                )
            if row.lease_until and row.lease_until > datetime.now(UTC):
                raise ApiError(
                    409, "conversation_busy", "The response is still being prepared. Try shortly."
                )
            pending = state.get("pending_result")
            if pending:
                if request.operation != "recover":
                    raise ApiError(
                        409,
                        "conversation_result_available",
                        "This request has saved work. Recover its result instead.",
                    )
                result = dict(pending)
            else:
                if request.operation != "cancel":
                    raise ApiError(
                        409,
                        "conversation_result_unavailable",
                        "No result was saved. Retry the original message or cancel "
                        "the unfinished request.",
                    )
                if await has_uncheckpointed_work(
                    session, owner, conversation_id, row.pending_request_id
                ):
                    raise ApiError(
                        409,
                        "conversation_work_exists",
                        "Work is linked to this request. Keep it for review; "
                        "start a new chat to continue.",
                    )
                result = {
                    "kind": "message",
                    "text": "The unfinished request was cancelled. You can continue this chat.",
                    "error_code": "conversation_request_cancelled",
                }
            result.update(
                conversation_id=row.id,
                version=row.version + 1,
                recovered_request_id=row.pending_request_id,
            )
            state["receipts"] = (
                state.get("receipts", [])
                + [
                    {
                        "request_id": row.pending_request_id,
                        "hash": row.pending_hash,
                        "response": result,
                    }
                ]
            )[-store.HISTORY_LIMIT :]
            if pending:
                # Do not invent user instructions from generated/provider content.
                # Consumers display this as an assistant-only recovered result.
                state["history"] = state["history"] + [
                    {
                        "user": "",
                        "assistant": result.get("text", ""),
                        "kind": result["kind"],
                        "recovered": True,
                        "request_id": row.pending_request_id,
                        "task_id": result.get("task_id"),
                        "proposal_id": result.get("proposal_id"),
                        "calendar_action_id": result.get("calendar_action_id"),
                        "error_code": result.get("error_code"),
                        "version": row.version + 1,
                        **state.get("pending_turn_clock", {}),
                    }
                ]
                await store.memory.persist_state(
                    session, row.id, state, current_request_id=row.pending_request_id
                )
            state.pop("pending_result", None)
            state.pop("pending_turn_clock", None)
            await store.goals.persist(session, row, state, row.version + 1)
            for key in ("calendar_read_request", "calendar_event_request"):
                if key == "calendar_event_request" and (
                    state.get(key, {}).get("last_request_id") != row.pending_request_id
                ):
                    # Cancelling a crashed detour does not cancel a different
                    # unfinished Calendar goal in the same conversation.
                    continue
                if (
                    not pending
                    or state.get(key, {}).get("last_request_id") != row.pending_request_id
                ):
                    state.pop(key, None)
            store.compact(state, preserve_receipt_id=row.pending_request_id)
            row.state_enc = store.encode(state)
            row.version += 1
            row.lease_id = row.lease_until = row.pending_request_id = row.pending_hash = None
    # Read current owned task/action state after releasing locks. No provider write,
    # approval change or cancellation of existing Calendar/Gmail work happens here.
    from app.conversation.service import hydrate_response

    return await hydrate_response(owner, result, factory)
