"""Owned goal identity is the boundary for read-only review and status guidance."""

import json

from sqlalchemy import select

from app.api.errors import ApiError
from app.auth.crypto import decrypt_token
from app.conversation import goals, store
from app.db.models import AssistantAction, ConversationGoal
from app.schemas.conversation import SelectConversationGoal


def clarification():
    return {"kind": "clarification", "text": "Which request would you like to review?"}


async def target(runtime, *, goal_id=None, source=None):
    """Resolve explicit IDs, or a unique retained request, never an arbitrary latest slot.

    Same-turn selection is already source/ownership validated. A durable focus is
    not enough to resolve an unbound reference among independent requests.
    """
    if source is not None and source != runtime.request.instruction:
        raise ApiError(
            422, "review_goal_source", "Copy the complete current USER turn into source."
        )
    goal_id = goal_id or getattr(runtime, "resumed_goal_id", None)
    async with runtime.factory() as session:
        chat = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        if goal_id:
            row = await session.get(ConversationGoal, (chat.id, goal_id))
            if row is None:
                raise ApiError(
                    404, "conversation_goal_missing", "That goal is no longer available."
                )
            rows = [row]
        else:
            # Bound decryption/read work. More candidates always means clarify.
            rows = (
                await session.scalars(
                    select(ConversationGoal)
                    .where(ConversationGoal.conversation_id == chat.id)
                    .limit(65)
                )
            ).all()
            if len(rows) == 65:
                return None
    candidates = {}
    for row in rows:
        payload = json.loads(decrypt_token(row.payload_enc))
        closed = row.status == "closed" or row.goal_id in runtime.state.get("closed_goal_ids", [])
        if closed and not (row.kind == "calendar_event" and payload["value"].get("action_id")):
            if goal_id:
                raise ApiError(
                    404, "conversation_goal_missing", "That goal is no longer available."
                )
            continue
        # Compose/reply goals may point at the same durable task. These are one
        # review target, not two independent requests.
        identity = payload["value"].get("task_id") or row.goal_id
        candidates[identity] = (row, payload, closed)
    return next(iter(candidates.values())) if len(candidates) == 1 else None


async def review(runtime, *, goal_id=None, source=None, presentation="status", email_only=False):
    selected = await target(runtime, goal_id=goal_id, source=source)
    if selected is None:
        return clarification()
    row, payload, closed = selected
    if email_only and row.kind not in {"email_draft", "reply", "task"}:
        raise ApiError(
            422,
            "review_goal_required",
            "Use review_conversation_goal with the intended owned goal_id and complete current "
            "USER source. Its kind determines the card and controls. "
            "Ask which request if ambiguous.",
        )
    # The explicit review response supplies its own card. Do not let service
    # decoration attach a different goal selected earlier in this model turn.
    runtime.reviewed_goal_kind = row.kind
    if row.kind == "calendar_event":
        # Review is independent of active focus and never reopens a closed goal.
        # A current, validated clear of action_id must win over older previews.
        value = payload["value"]
        current = runtime.state.get(goals.KEYS[row.kind])
        staged = runtime.state.get(goals.UPDATES, {}).get(row.goal_id)
        if not closed and current and current.get("goal_id") == row.goal_id:
            value = current
        elif not closed and staged and staged["kind"] == row.kind:
            value = staged["value"]
        if action_id := value.get("action_id"):
            from app.assistant import source_data
            from app.calendar.event_creation import response
            from app.mail.dependency import references

            await source_data.prefetch(
                runtime.owner, await references(runtime.owner, [action_id], factory=runtime.factory)
            )

            async with runtime.factory() as session:
                action = await session.get(AssistantAction, action_id)
                if (
                    action is None
                    or action.user_id != runtime.owner
                    or action.source_versions.get("conversation_id") != row.conversation_id
                ):
                    raise ApiError(
                        404, "review_action_missing", "That event is no longer available."
                    )
                return await response(session, runtime.owner, action_id)
        return {
            "kind": "message",
            "text": "This event still needs details before it can be reviewed.",
        }
    if email_only and row.kind == "task":
        from app.assistant import draft_review, tasks

        async with runtime.factory() as session:
            task = await tasks.owned_task(session, runtime.owner, payload["value"]["task_id"])
            artifact = (
                await draft_review.owned_artifact(session, runtime.owner, task.final_artifact_id)
                if task.final_artifact_id
                else None
            )
        if artifact is None or artifact.payload.get("kind") != "draft":
            return clarification()
    await goals.select_goal(
        runtime, SelectConversationGoal(goal_id=row.goal_id, source=runtime.request.instruction)
    )
    if row.kind in {"email_draft", "reply", "task"}:
        from app.conversation import email_review

        if runtime.artifact and runtime.artifact.payload.get("kind") == "draft":
            return await email_review.selected(runtime, presentation=presentation)
        if row.kind == "email_draft" and not runtime.active:
            return await email_review.selected(runtime, presentation=presentation)
        if runtime.active:
            return {
                "kind": "message",
                "text": "Here is the current status of that request.",
                "task_id": runtime.state["active_task_id"],
                "task": runtime.active,
            }
    if row.kind == "proposal":
        return {
            "kind": "message",
            "text": "Here is your existing proposal.",
            "proposal_id": runtime.state["proposal_id"],
            "proposal": runtime.resumed_proposal,
        }
    return {"kind": "message", "text": "That request has no reviewable result yet."}


async def calendar_status(runtime):
    """Guard prose shortcuts with the same target boundary as explicit tools."""
    selected = await target(runtime)
    if selected is None or selected[0].kind != "calendar_event":
        return clarification()
    return await review(runtime, goal_id=selected[0].goal_id, source=runtime.request.instruction)
