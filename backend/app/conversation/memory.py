"""Bounded retrieval from this owned chat; remembered content grants no authority.

The encrypted turn archive is the durable dialogue, not a provider cache. Active
state remains in Conversation; there is no process-wide or cross-chat memory.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.assistant.summary import digest
from app.auth.crypto import decrypt_token, encrypt_token
from app.db.models import ConversationExchange

PAGE_SIZE = 40
RESULT_CHARS = 16_000
RECENT_CHARS = 24_000
REFERENCE_FIELDS = ("message_id", "thread_id", "context_id", "remembered_scope")


def reference_name(version, reference):
    # Existing read/workflow schemas cap handles at 40 characters. Re-archiving
    # an already recalled source must not grow a chain of history prefixes.
    return f"history-{version}-{digest(reference)[:12]}"


async def persist_state(session, conversation_id, state, *, current_request_id=None):
    archived = state.get("archived_through_version", 0)
    entries = [e for e in state.get("history", []) if e.get("version", 0) > archived]
    await persist(
        session,
        conversation_id,
        entries,
        state.get("refs", {}),
        current_request_id=current_request_id,
    )
    state["archived_through_version"] = max(
        (e.get("version", 0) for e in entries), default=archived
    )
    if entries:
        state.setdefault("archived_from_version", min(e["version"] for e in entries))


async def persist(session, conversation_id, history, references, *, current_request_id=None):
    rows = []
    for entry in history:
        if not entry.get("version") or not entry.get("request_id"):
            continue
        # Only identity handles are archived; no snippets or tool observations.
        source_refs = {
            name: {key: value[key] for key in REFERENCE_FIELDS if value.get(key)}
            for name, value in references.items()
            if (name == "selected" and entry["request_id"] == current_request_id)
            or (name != "selected" and name in entry.get("context_references", []))
        }
        payload = {"exchange": entry, "source_references": source_refs}
        rows.append(
            {
                "conversation_id": conversation_id,
                "version": entry["version"],
                "request_id": entry["request_id"],
                "payload_enc": encrypt_token(json.dumps(payload, ensure_ascii=False)),
            }
        )
    if rows:
        await session.execute(insert(ConversationExchange).values(rows).on_conflict_do_nothing())


def context(state, version):
    history = state.get("history", [])
    return {
        "scope": "this_conversation_only",
        "retention": "until this chat expires or is deleted; at most seven days",
        "completed_turns": version,
        "active_goal": state.get("active_goal"),
        "archive_from_version": state.get("archived_from_version"),
        "recent_from_version": min(
            (e.get("version", version + 1) for e in history), default=version + 1
        ),
        "older_context_tool": "recall_conversation",
        "note": (
            "Recent context is a window, not the whole chat. Recall earlier user details "
            "when needed. Remembered source references require fresh reads. Old goals do "
            "not become active merely because they were recalled."
        ),
    }


def recent(history):
    """A bounded model view; the durable exchange is never truncated here."""
    from app.conversation.runtime import model_history

    result, size = [], 0
    for raw in reversed(model_history(history[-12:])):
        entry = dict(raw)
        if len(entry.get("assistant", "")) > 2000:
            entry["assistant"] = entry["assistant"][:2000]
            entry["assistant_truncated"] = True
        if entry.get("email_draft"):
            entry["email_draft"] = {
                key: entry["email_draft"].get(key) for key in ("draft_id", "recipient", "subject")
            }
        cost = len(json.dumps(entry, ensure_ascii=False))
        if size + cost > RECENT_CHARS:
            break
        size += cost
        result.append(entry)
    return list(reversed(result))


async def recall(runtime, args):
    from app.conversation import store
    from app.conversation.runtime import model_history

    async with runtime.factory() as session:
        row = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        query = (
            select(ConversationExchange)
            .where(
                ConversationExchange.conversation_id == row.id,
                ConversationExchange.version < (args.before_version or row.version + 1),
            )
            .order_by(ConversationExchange.version.desc())
            .limit(PAGE_SIZE)
        )
        if args.versions:
            query = (
                select(ConversationExchange)
                .where(
                    ConversationExchange.conversation_id == row.id,
                    ConversationExchange.version.in_(args.versions),
                    ConversationExchange.version <= row.version,
                )
                .order_by(ConversationExchange.version.desc())
                .limit(4)
            )
        archived = (await session.scalars(query)).all()
    # Include retained pre-upgrade turns until their first durable archive write.
    values = {item.version: json.loads(decrypt_token(item.payload_enc)) for item in archived}
    for entry in store.decode(row).get("history", []):
        # Pre-version history remains visible in recent dialogue, but cannot be
        # cited as a fabricated archive version or crash a paginated recall.
        if not isinstance(entry.get("version"), int) or entry["version"] < 1:
            continue
        if entry["version"] < (args.before_version or row.version + 1) and (
            not args.versions or entry.get("version") in args.versions
        ):
            values.setdefault(entry["version"], {"exchange": entry, "source_references": {}})
    words = re.findall(r"\w+", args.query.casefold())
    scanned = sorted(values, reverse=True)[:PAGE_SIZE]
    index = [
        {"version": version, "user_excerpt": values[version]["exchange"].get("user", "")[:160]}
        for version in reversed(scanned)
    ]
    results, size = [], len(json.dumps(index, ensure_ascii=False))
    for version in scanned:
        value = values[version]
        entry = model_history([value["exchange"]])[0]
        haystack = (entry.get("user", "") + " " + entry.get("assistant", "")).casefold()
        if words and not all(word in haystack for word in words):
            continue
        result = {
            key: entry.get(key)
            for key in (
                "version",
                "recorded_at",
                "timezone",
                "request_id",
                "user",
                "assistant",
                "kind",
                "source",
                "task_id",
                "proposal_id",
                "calendar_action_id",
                "error_code",
            )
        }
        result["assistant"] = (result.get("assistant") or "")[:2000]
        result["assistant_truncated"] = len(entry.get("assistant", "")) > 2000
        result["source_references"] = []
        result["source_scopes"] = {}
        for reference in value.get("source_references", {}):
            name = reference_name(version, reference)
            result["source_references"].append(name)
            result["source_scopes"][name] = value["source_references"][reference].get(
                "remembered_scope", "unknown"
            )
        cost = len(json.dumps(result, ensure_ascii=False))
        if size + cost > RESULT_CHARS:
            break
        for reference, handle in value.get("source_references", {}).items():
            runtime.state["refs"][reference_name(version, reference)] = handle
        results.append(result)
        size += cost
        if len(results) == args.limit:
            break
    return {
        "scope": "this_conversation_only",
        "exchanges": list(reversed(results)),
        "turn_index": index,
        "next_before_version": min(scanned)
        if scanned and min(scanned) > 1 and not args.versions
        else None,
        "coverage": "bounded_page",
        "notice": (
            "Historical user/assistant dialogue, not fresh provider facts or approval. "
            "Re-read source references before source-dependent work. Ask which goal "
            "to resume if ambiguous."
        ),
    }


async def resume_task(runtime, args):
    """Select existing work in this chat. Selection never executes or approves it."""
    from app.api.errors import ApiError
    from app.api.routes.assistant import task_view
    from app.assistant import draft_review, tasks
    from app.conversation import store

    if args.source.strip() != runtime.request.instruction.strip():
        raise ValueError("Copy the complete current USER turn when choosing work to resume")
    async with runtime.factory() as session:
        row = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        exchange = await session.get(ConversationExchange, (row.id, args.turn_version))
        entry = (
            json.loads(decrypt_token(exchange.payload_enc))["exchange"]
            if exchange
            else next(
                (
                    e
                    for e in store.decode(row).get("history", [])
                    if e.get("version") == args.turn_version
                ),
                {},
            )
        )
        if not entry.get("task_id"):
            raise ApiError(
                404, "conversation_work_missing", "That exchange has no saved task to resume."
            )
        task = await tasks.owned_task(session, runtime.owner, entry["task_id"])
        active = await task_view(session, task)
        artifact = (
            await draft_review.owned_artifact(session, runtime.owner, task.final_artifact_id)
            if task.final_artifact_id
            else None
        )
    runtime.state["active_task_id"] = task.id
    runtime.state.pop("proposal_id", None)
    runtime.state["active_goal"] = "saved_task"
    runtime.active, runtime.artifact = active, artifact
    runtime.resumed_task_id = task.id
    return {
        "active_work": {
            key: active.get(key)
            for key in (
                "task_id",
                "instruction",
                "state",
                "question",
                "error_code",
                "resolved_inputs",
            )
        },
        "current_artifact": {
            "kind": artifact.payload["kind"],
            "revision": artifact.revision,
            "content": artifact.payload["content"],
        }
        if artifact
        else None,
        "notice": (
            "Resumed this chat's saved work without execution or approval. Generated output "
            "is historical; re-read sources for new source-dependent work."
        ),
    }
