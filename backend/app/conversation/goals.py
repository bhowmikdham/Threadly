"""Per-chat goal registry. Selecting focus neither executes work nor grants approval."""

import json
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import and_, or_, select

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.auth.crypto import decrypt_token, encrypt_token
from app.db.models import AssistantTask, CommandPlan, ConversationGoal

KEYS = {
    "calendar_event": "calendar_event_request",
    "email_draft": "email_draft_goal",
    "reply": "mail_reply_goal",
    "mail_search": "mail_goal",
}
REFERENCE_FIELDS = ("thread_id", "message_id", "context_id", "remembered_scope")


def close(state, value):
    if value and value.get("goal_id"):
        state.setdefault("closed_goal_ids", []).append(value["goal_id"])


async def persist(session, chat, state, version):
    pointers = state.setdefault("goal_pointers", {})
    for kind, key in KEYS.items():
        value = state.get(key)
        if not value:
            pointers.pop(kind, None)
            continue
        identifier = value.setdefault("goal_id", str(uuid4()))
        pointers[kind] = identifier
        label = (
            value.get("arguments", {}).get("title")
            or value.get("recipient")
            or value.get("instruction")
            or value.get("creation_origin", {}).get("text")
            or kind
        )[:160]
        refs = {}
        if kind == "reply" and value.get("reference") and value.get("source_identity"):
            name = value["reference"]
            refs[name] = {k: v for k, v in value["source_identity"].items() if v}
            current = state.get("refs", {}).get(name, {})
            if all(current.get(k) == v for k, v in refs[name].items()):
                refs[name].update({k: current[k] for k in REFERENCE_FIELDS if current.get(k)})
        payload = {"value": deepcopy(value), "label": label, "references": refs}
        existing = await session.get(ConversationGoal, (chat.id, identifier))
        if kind == "email_draft" and value.get("status") == "drafted" and not value.get("draft"):
            # Compaction may retain the draft only in the UI window. The goal
            # registry keeps its full generated text independently of that cache.
            for entry in reversed(state.get("history", [])):
                draft = entry.get("email_draft")
                if draft and draft.get("draft_id") == value.get("draft_id"):
                    payload["value"]["draft"] = {
                        k: draft[k] for k in ("subject", "body", "unresolved_fields")
                    }
                    payload["value"]["draft"]["sources"] = []
                    break
            if "draft" not in payload["value"] and existing:
                old = json.loads(decrypt_token(existing.payload_enc))["value"]
                if old.get("draft_id") == value.get("draft_id") and old.get("draft"):
                    payload["value"]["draft"] = old["draft"]
        hashed = digest(payload)
        if existing is None:
            session.add(
                ConversationGoal(
                    conversation_id=chat.id,
                    goal_id=identifier,
                    kind=kind,
                    status="retained",
                    updated_version=max(1, version),
                    payload_hash=hashed,
                    payload_enc=encrypt_token(json.dumps(payload, ensure_ascii=False)),
                )
            )
        elif existing.payload_hash != hashed:
            existing.updated_version = max(1, version)
            existing.payload_hash = hashed
            existing.payload_enc = encrypt_token(json.dumps(payload, ensure_ascii=False))
    # A durable task is already an independent goal; keep an identity-only pointer.
    if identifier := state.get("active_task_id"):
        task = await session.get(AssistantTask, identifier)
        if task is not None and task.user_id == chat.user_id:
            existing = await session.get(ConversationGoal, (chat.id, identifier))
            if existing is None:
                payload = {
                    "value": {"task_id": identifier},
                    "label": task.instruction[:160],
                    "references": {},
                }
                session.add(
                    ConversationGoal(
                        conversation_id=chat.id,
                        goal_id=identifier,
                        kind="task",
                        status="retained",
                        updated_version=max(1, version),
                        payload_hash=digest(payload),
                        payload_enc=encrypt_token(json.dumps(payload, ensure_ascii=False)),
                    )
                )
            pointers["task"] = identifier
    else:
        pointers.pop("task", None)
    if identifier := state.get("proposal_id"):
        proposal = await session.get(CommandPlan, identifier)
        if proposal is not None and proposal.user_id == chat.user_id:
            existing = await session.get(ConversationGoal, (chat.id, identifier))
            if existing is None:
                payload = {
                    "value": {"proposal_id": identifier},
                    "label": proposal.request.get("instruction", "Saved proposal")[:160],
                    "references": {},
                }
                session.add(
                    ConversationGoal(
                        conversation_id=chat.id,
                        goal_id=identifier,
                        kind="proposal",
                        status="retained",
                        updated_version=max(1, version),
                        payload_hash=digest(payload),
                        payload_enc=encrypt_token(json.dumps(payload, ensure_ascii=False)),
                    )
                )
            pointers["proposal"] = identifier
    else:
        pointers.pop("proposal", None)
    for identifier in state.pop("closed_goal_ids", []):
        record = await session.get(ConversationGoal, (chat.id, identifier))
        if record:
            record.status = "closed"
            record.updated_version = max(1, version)
    kind = state.get("active_goal")
    state["active_goal_id"] = pointers.get("task" if kind == "saved_task" else kind)


async def listing(
    runtime, *, before_version=None, after_goal_id=None, limit=8, include_closed=False
):
    from app.conversation import store

    async with runtime.factory() as session:
        chat = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        query = select(ConversationGoal).where(ConversationGoal.conversation_id == chat.id)
        if not include_closed:
            query = query.where(ConversationGoal.status == "retained")
        if before_version is not None:
            query = query.where(
                or_(
                    ConversationGoal.updated_version < before_version,
                    and_(
                        ConversationGoal.updated_version == before_version,
                        ConversationGoal.goal_id > after_goal_id,
                    )
                    if after_goal_id
                    else False,
                )
            )
        rows = (
            await session.scalars(
                query.order_by(
                    ConversationGoal.updated_version.desc(), ConversationGoal.goal_id
                ).limit(limit)
            )
        ).all()
    return {
        "goals": [
            {
                "goal_id": row.goal_id,
                "kind": row.kind,
                "status": row.status,
                "label": json.loads(decrypt_token(row.payload_enc))["label"],
                "updated_version": row.updated_version,
                "focused": row.goal_id == runtime.state.get("active_goal_id"),
            }
            for row in rows
        ],
        "next_cursor": {
            "before_version": rows[-1].updated_version,
            "after_goal_id": rows[-1].goal_id,
        }
        if len(rows) == limit
        else None,
        "notice": (
            "Retained work is not automatically active or approved. Ask which goal if ambiguous."
        ),
    }


async def select_goal(runtime, args):
    from app.api.routes.assistant import task_view
    from app.assistant import command_plans, coordinator, draft_review, tasks
    from app.conversation import email_draft, store

    if args.source != runtime.request.instruction:
        raise ValueError("Copy the complete current USER turn when selecting a goal")
    async with runtime.factory() as session:
        chat = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        row = await session.get(ConversationGoal, (chat.id, args.goal_id))
        if row is None or row.status == "closed":
            raise ApiError(404, "conversation_goal_missing", "That goal is no longer available.")
        payload = json.loads(decrypt_token(row.payload_enc))
        kind = row.kind
    value = payload["value"]
    if kind in KEYS:
        runtime.state[KEYS[kind]] = value
    for original, reference in payload["references"].items():
        # A restored goal source is independent of the browser's explicit pin.
        # Stable, collision-resistant handles also avoid overwriting search refs.
        handle = "goal-" + digest([row.goal_id, reference])[:24]
        runtime.state["refs"][handle] = reference
        if value.get("reference") == original:
            value["reference"] = handle
    runtime.state["active_goal"] = kind if kind != "task" else "saved_task"
    runtime.state["active_goal_id"] = row.goal_id
    runtime.state.pop("proposal_id", None)
    runtime.state.pop("active_task_id", None)
    runtime.active = runtime.artifact = None
    if task_id := value.get("task_id"):
        async with runtime.factory() as session:
            task = await tasks.owned_task(session, runtime.owner, task_id)
            runtime.active = await task_view(session, task)
            if task.final_artifact_id:
                runtime.artifact = await draft_review.owned_artifact(
                    session, runtime.owner, task.final_artifact_id
                )
        runtime.state["active_task_id"] = task_id
        runtime.resumed_task_id = task_id
    runtime.resumed_goal_id = row.goal_id
    if proposal_id := value.get("proposal_id"):
        async with runtime.factory() as session:
            proposal = await coordinator.owned(session, runtime.owner, proposal_id)
            runtime.resumed_proposal = await command_plans.view(session, proposal)
        runtime.state["proposal_id"] = proposal_id
    return {
        "goal_id": row.goal_id,
        "kind": kind,
        "pending": value,
        "active_work": runtime.active,
        "email_draft": email_draft.current_text_draft(runtime.state),
        "notice": "Selected retained work. No execution or approval occurred.",
    }
