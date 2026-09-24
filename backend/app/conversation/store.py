"""Short transactions, turn leases, optimistic versions and bounded encrypted history."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.auth.crypto import decrypt_token, encrypt_token
from app.config import get_settings
from app.db.models import Conversation, User

TTL = timedelta(days=7)
LEASE = timedelta(seconds=180)
HISTORY_LIMIT = 12
STATE_PLAINTEXT_LIMIT = 80_000
STATE_ENCRYPTED_LIMIT = 110_000
COMPACT_ASSISTANT_LIMIT = 1_000


def _raw(state):
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _plaintext_size(state):
    return len(_raw(state).encode())


def compact(state, *, preserve_receipt_id=None):
    """Deterministically fit durable state while retaining current recovery data."""
    state["history"] = state.get("history", [])[-HISTORY_LIMIT:]
    state["receipts"] = state.get("receipts", [])[-HISTORY_LIMIT:]

    # Old receipts are convenience replays. The in-flight/current receipt is the
    # recovery boundary and must remain byte-for-byte exact.
    index = 0
    while _plaintext_size(state) > STATE_PLAINTEXT_LIMIT and index < len(state["receipts"]):
        if state["receipts"][index].get("request_id") == preserve_receipt_id:
            index += 1
        else:
            state["receipts"].pop(index)

    # Model history is context, not an idempotency receipt. Compact assistant text
    # first, then evict oldest exchanges while keeping the newest exchange.
    if _plaintext_size(state) > STATE_PLAINTEXT_LIMIT:
        for entry in state["history"]:
            text = entry.get("assistant") or ""
            if len(text) > COMPACT_ASSISTANT_LIMIT:
                entry["assistant"] = text[: COMPACT_ASSISTANT_LIMIT - 1] + "…"
    while _plaintext_size(state) > STATE_PLAINTEXT_LIMIT and len(state["history"]) > 1:
        state["history"].pop(0)

    # Pagination is recoverable by repeating a search. Source identity and the
    # exact current receipt/pending result take precedence at the hard boundary.
    if _plaintext_size(state) > STATE_PLAINTEXT_LIMIT:
        state.pop("search", None)
    if _plaintext_size(state) > STATE_PLAINTEXT_LIMIT:
        selected = state.get("refs", {}).get("selected")
        state["refs"] = {"selected": selected} if selected else {}
        state["result_order"] = []
    if _plaintext_size(state) > STATE_PLAINTEXT_LIMIT:
        raise ApiError(422, "conversation_limit", "Start a new conversation to continue.")
    return state


def encode(state):
    raw = _raw(state)
    if len(raw.encode()) > STATE_PLAINTEXT_LIMIT:
        raise ApiError(422, "conversation_limit", "Start a new conversation to continue.")
    encrypted = encrypt_token(raw)
    if len(encrypted) > STATE_ENCRYPTED_LIMIT:
        raise ApiError(422, "conversation_limit", "Start a new conversation to continue.")
    return encrypted


def decode(row):
    return json.loads(decrypt_token(row.state_enc))


def request_hash(request):
    """Hash values and presence; omitted source and explicit null have different meaning."""
    return digest(
        {
            "payload": request.model_dump(),
            "present_fields": sorted(request.model_fields_set),
        }
    )


async def enforce_admission(session, owner, row, request, hashed, now):
    settings = get_settings()
    active = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.user_id == owner, Conversation.lease_until > now)
    )
    if active >= settings.conversation_max_active_per_user:
        raise ApiError(429, "conversation_capacity", "Too many responses are running. Try shortly.")
    retrying = row.pending_request_id == request.request_id and row.pending_hash == hashed
    if retrying:
        return
    rows = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.user_id == owner, Conversation.expires_at > now)
    )
    if rows > settings.conversation_max_rows_per_user:
        raise ApiError(429, "conversation_history_limit", "Delete an old chat before starting one.")
    completed = await session.scalar(
        select(func.coalesce(func.sum(Conversation.version), 0))
        .select_from(Conversation)
        .where(Conversation.user_id == owner, Conversation.expires_at > now)
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.user_id == owner,
            Conversation.expires_at > now,
            Conversation.pending_request_id.is_not(None),
        )
    )
    if completed + pending >= settings.conversation_max_retained_turns:
        raise ApiError(
            429,
            "conversation_turn_limit",
            "This account reached its retained conversation-turn limit. Delete old chats.",
        )


async def owned(session, owner, identifier, *, lock=False):
    query = select(Conversation).where(Conversation.id == identifier, Conversation.user_id == owner)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None or row.expires_at <= datetime.now(UTC):
        raise ApiError(
            404, "conversation_not_found", "This conversation expired. Start a new chat."
        )
    user = await session.get(User, owner)
    if user is None or user.google_account_version != row.account_version:
        raise ApiError(
            409, "conversation_account_changed", "Your connection changed. Start a new chat."
        )
    return row


async def claim(session, owner, request):
    now = datetime.now(UTC)
    user = await session.get(User, owner, with_for_update=True)
    if not user:
        raise ApiError(401, "unauthorized", "Sign in again.")
    if request.expected_version == 0:
        await session.execute(
            insert(Conversation)
            .values(
                id=request.conversation_id,
                user_id=owner,
                account_version=user.google_account_version,
                version=0,
                state_enc=encode({"history": [], "refs": {}, "result_order": []}),
                expires_at=now + TTL,
            )
            .on_conflict_do_nothing()
        )
    row = await owned(session, owner, request.conversation_id, lock=True)
    state = decode(row)
    hashed = request_hash(request)
    for saved in state.get("receipts", []):
        if saved["request_id"] == request.request_id:
            if saved["hash"] != hashed:
                raise ApiError(409, "idempotency_conflict", "That turn ID was already used.")
            return row, state, None, saved["response"]
    if row.version != request.expected_version:
        raise ApiError(
            409, "conversation_version_conflict", "This chat changed. Reload it before continuing."
        )
    if row.lease_until and row.lease_until > now:
        raise ApiError(409, "conversation_busy", "The previous response is still being prepared.")
    if row.pending_request_id == request.request_id and row.pending_hash != hashed:
        raise ApiError(409, "idempotency_conflict", "That turn ID was already used.")
    if row.pending_request_id and (
        row.pending_request_id != request.request_id or row.pending_hash != hashed
    ):
        # A crashed request may already have created a draft job. Resolve/retry that request
        # before advancing, so it cannot become detached from the conversation.
        raise ApiError(409, "conversation_retry_required", "Retry the unfinished message first.")
    await enforce_admission(session, owner, row, request, hashed, now)
    lease = str(uuid4())
    row.lease_id, row.lease_until = lease, now + LEASE
    if row.expires_at < now + LEASE:
        row.expires_at = now + LEASE
    row.pending_request_id, row.pending_hash = request.request_id, hashed
    return row, state, lease, None


async def complete(session, owner, request, lease, state, response):
    row = await owned(session, owner, request.conversation_id, lock=True)
    if row.lease_id != lease or row.lease_until <= datetime.now(UTC):
        raise ApiError(
            409, "conversation_lease_lost", "Reload this conversation before continuing."
        )
    # Search snippets and raw tool observations are NEVER stored. Only source IDs,
    # user dialogue, assistant answers and generated task/proposal references survive.
    saved = {
        k: v for k, v in response.items() if k not in {"search", "task", "artifacts", "proposal"}
    }
    saved.update(conversation_id=row.id, version=row.version + 1)
    state["history"] = (
        state["history"]
        + [
            {
                "user": request.instruction,
                "assistant": saved.get("text", ""),
                "kind": saved["kind"],
                "task_id": saved.get("task_id"),
                "proposal_id": saved.get("proposal_id"),
                "request_id": request.request_id,
            }
        ]
    )[-HISTORY_LIMIT:]
    state["receipts"] = (
        state.get("receipts", [])
        + [
            {
                "request_id": request.request_id,
                "hash": row.pending_hash,
                "response": saved,
            }
        ]
    )[-HISTORY_LIMIT:]
    state.pop("pending_result", None)
    compact(state, preserve_receipt_id=request.request_id)
    row.state_enc = encode(state)
    row.version += 1
    row.lease_id = row.lease_until = row.pending_request_id = row.pending_hash = None
    return {**response, "conversation_id": row.id, "version": row.version}


async def release_failed(session, owner, identifier, lease):
    row = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == identifier,
            Conversation.user_id == owner,
            Conversation.lease_id == lease,
        )
        .with_for_update()
    )
    if row:
        row.lease_until = datetime.now(UTC)
        row.lease_id = None


async def purge(session):
    now = datetime.now(UTC)
    await session.execute(
        delete(Conversation).where(
            Conversation.expires_at <= now,
            or_(Conversation.lease_until.is_(None), Conversation.lease_until <= now),
        )
    )


async def checkpoint(session, owner, request, lease, state, response):
    row = await owned(session, owner, request.conversation_id, lock=True)
    if row.lease_id != lease or row.lease_until <= datetime.now(UTC):
        raise ApiError(409, "conversation_lease_lost", "Retry this message.")
    state["pending_result"] = {
        k: v for k, v in response.items() if k not in {"search", "task", "artifacts", "proposal"}
    }
    compact(state)
    row.state_enc = encode(state)
