"""Reference-only captures with request/attempt-scoped, in-memory hydration.

Callers prefetch outside database transactions. `context_data` is deliberately
synchronous and cannot make hidden HTTP calls while approval/task locks are held.
No process-global mail cache or ORM assignment of hydrated text is used.
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.db.models import ContextSnapshot, Thread, User
from app.mail import live

STORAGE = "gmail-reference-1.0"
_cache = ContextVar("gmail_request_sources", default=None)


def is_reference(payload):
    return isinstance(payload, dict) and payload.get("storage") == STORAGE


@asynccontextmanager
async def source_scope():
    token = _cache.set({})
    try:
        yield
    finally:
        _cache.reset(token)


async def fetch(owner, thread_id):
    cache = _cache.get()
    if cache is None:
        raise ApiError(503, "source_scope_missing", "Start a new source request.")
    key = (owner, thread_id)
    if key not in cache:
        cache[key] = await live.thread(owner, thread_id)
    return cache[key]


def source_for(owner, thread_id):
    source = (_cache.get() or {}).get((owner, thread_id))
    if source is None:
        raise ApiError(409, "source_not_loaded", "Select the source again before continuing.")
    return source


def materialize(ref, source):
    if ref["owner_id"] != source["owner"] or ref["account_version"] != source["account_version"]:
        raise ApiError(409, "google_connection_changed", "Select the source again.")
    if ref["fingerprint"] != source["fingerprint"]:
        raise ApiError(409, "source_changed", "The Gmail thread changed; capture it again.")
    messages = source["messages"]
    ui = ref.get("ui_map")
    selected = (
        messages[-50:]
        if ui is None
        else [m for m in messages if m["gmail_msg_id"] in ui["visible_message_ids"]]
    )
    if ui and {m["gmail_msg_id"] for m in selected} != set(ui["visible_message_ids"]):
        raise ApiError(404, "ui_reference_not_found", "A visible message is not in this thread.")
    if ui:
        order = {identifier: i for i, identifier in enumerate(ui["visible_message_ids"])}
        selected.sort(key=lambda m: order[m["gmail_msg_id"]])
    remaining, truncated = 12000, []
    output = []
    # Keep newest messages in a full-thread capture; UI captures divide their budget.
    for m in reversed(selected) if ui is None else selected:
        if remaining <= 0:
            break
        limit = remaining if ui is None else 12000 // len(selected)
        body = m["body_clean"][:limit]
        if len(body) < len(m["body_clean"]):
            truncated.append(m["gmail_msg_id"])
        output.append(
            {
                "message_id": m["gmail_msg_id"],
                "from_addr": m["from_addr"],
                "sent_at": m["sent_at"],
                "body": body,
            }
        )
        remaining -= len(body)
    if ui is None:
        output.reverse()
    if not any(m["body"].strip() for m in output):
        raise ApiError(409, "context_empty", "Selected source has no usable text.")
    result = {
        "schema_version": "1.1" if ui else "1.0",
        "scope": "gmail_on_demand_excerpts",
        "source_mode": "gmail_on_demand",
        "owner_id": ref["owner_id"],
        "account_version": ref["account_version"],
        "fingerprint": ref["fingerprint"],
        "thread_id": ref["thread_id"],
        "thread_version": ref["thread_version"],
        "messages": output,
        "total_synced_messages": len(messages),
        "omitted_messages": len(messages) - len(output),
        "truncated_messages": len(truncated),
    }
    if ui:
        result.update(
            ui_map=ui,
            truncated_message_ids=truncated,
            capture_policy="gmail-on-demand-ui-1.0:50-ids:12000-chars",
        )
    return result


def context_data(context):
    payload = context.payload
    if not is_reference(payload):
        from app.config import get_settings

        if get_settings().gmail_source_mode == "on_demand":
            raise ApiError(
                409, "legacy_source_retired", "Recapture this source directly from Gmail."
            )
        return payload
    if payload["owner_id"] != context.user_id:
        raise ApiError(404, "context_not_found", "Unknown context.")
    result = materialize(payload, source_for(context.user_id, payload["thread_id"]))
    if digest(result) != context.source_hash:
        raise ApiError(409, "source_changed", "Capture source again.")
    return result


async def prefetch(owner, references):
    for ref in references:
        if is_reference(ref):
            if ref["owner_id"] != owner:
                raise ApiError(404, "context_not_found", "Unknown context.")
            source = await fetch(owner, ref["thread_id"])
            materialize(ref, source)


async def capture(session, owner, thread_id, ui_map=None):
    # Route prefetches before opening its transaction. No emails enter ORM rows.
    source = source_for(owner, thread_id)
    user = await session.get(User, owner, with_for_update=True)
    if (
        not user
        or not user.google_connected
        or user.google_account_version != source["account_version"]
    ):
        raise ApiError(409, "google_connection_changed", "Select source again.")
    if ui_map:
        captured = datetime.fromisoformat(ui_map.captured_at)
        now = datetime.now(UTC)
        if captured < now - timedelta(minutes=15) or captured > now + timedelta(minutes=5):
            raise ApiError(409, "ui_capture_expired", "Capture the current view again.")
    await session.execute(
        insert(Thread)
        .values(user_id=owner, gmail_thread_id=thread_id, version=1)
        .on_conflict_do_nothing()
    )
    thread = await session.scalar(
        select(Thread)
        .where(Thread.user_id == owner, Thread.gmail_thread_id == thread_id)
        .with_for_update()
    )
    previous = await session.scalar(
        select(ContextSnapshot)
        .where(ContextSnapshot.user_id == owner, ContextSnapshot.thread_id == thread.id)
        .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.id.desc())
        .limit(1)
    )
    if previous and (
        previous.payload.get("fingerprint") != source["fingerprint"]
        or previous.payload.get("account_version") != source["account_version"]
    ):
        thread.version += 1
    if ui_map and ui_map.thread_version != thread.version:
        raise ApiError(409, "ui_context_changed", "Thread changed; capture its current version.")
    thread.last_msg_id = source["messages"][-1]["gmail_msg_id"]
    # Thread row holds source IDs/version only, never subject/body/addresses.
    ref = {
        "storage": STORAGE,
        "owner_id": owner,
        "account_version": source["account_version"],
        "thread_id": thread_id,
        "thread_version": thread.version,
        "fingerprint": source["fingerprint"],
        "ui_map": ui_map.model_dump() if ui_map else None,
    }
    data = materialize(ref, source)
    result = ContextSnapshot(
        id=str(uuid4()), user_id=owner, thread_id=thread.id, source_hash=digest(data), payload=ref
    )
    session.add(result)
    await session.flush()
    return result


async def validate(session, owner, data):
    source = source_for(owner, data["thread_id"])
    if (
        source["owner"] != owner
        or data.get("owner_id") != owner
        or source["fingerprint"] != data.get("fingerprint")
    ):
        raise ApiError(409, "source_changed", "Capture the current source again.")
    user = await session.get(User, owner, populate_existing=True)
    if (
        not user
        or not user.google_connected
        or user.google_account_version != data["account_version"]
    ):
        raise ApiError(409, "google_connection_changed", "Reconnect and select source again.")


def reply_row(owner, thread_id, message_id, version):
    from types import SimpleNamespace

    source = source_for(owner, thread_id)
    values = next((m for m in source["messages"] if m["gmail_msg_id"] == message_id), None)
    if values is None:
        raise ApiError(409, "reply_context_changed", "Select an available reply message.")
    return SimpleNamespace(
        Message=SimpleNamespace(**values), Thread=SimpleNamespace(version=version), version=version
    )
