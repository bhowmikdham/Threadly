"""Only authenticated UI requests can change chat-scoped Calendar permission."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import store
from app.db.engine import get_session_factory
from app.db.models import Conversation, User


def identifier(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise ApiError(422, "invalid_conversation", "Start a new chat.") from None


def view(row, user):
    valid = row and row.calendar_approval_session_version == user.threadly_session_version
    return {
        "mode": row.calendar_approval_mode if valid else "ask",
        "version": row.calendar_approval_version if row else 0,
        "scope": "calendar_events_this_chat",
    }


async def get(owner, conversation_id, *, factory=None):
    async with (factory or get_session_factory())() as session:
        user = await session.get(User, owner)
        row = await session.get(Conversation, identifier(conversation_id))
        if row:
            row = await store.owned(session, owner, row.id)
        return view(row, user)


async def set_mode(owner, conversation_id, request, *, factory=None):
    async with (factory or get_session_factory()).begin() as session:
        # Same account fence as the write worker. A committed revocation prevents
        # queued automatic approval dispatch; already dispatched writes reconcile.
        user = await session.get(User, owner, with_for_update=True)
        if not user:
            raise ApiError(401, "unauthorized", "Sign in again.")
        cid = identifier(conversation_id)
        existing = await session.get(Conversation, cid)
        if existing is None:
            count = await session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.user_id == owner, Conversation.expires_at > datetime.now(UTC))
            )
            if count >= get_settings().conversation_max_rows_per_user:
                raise ApiError(429, "conversation_history_limit", "Delete an old chat first.")
        await session.execute(
            insert(Conversation)
            .values(
                id=cid,
                user_id=owner,
                account_version=user.google_account_version,
                version=0,
                state_enc=store.encode({"history": [], "refs": {}, "result_order": []}),
                expires_at=datetime.now(UTC) + store.TTL,
            )
            .on_conflict_do_nothing()
        )
        row = await store.owned(session, owner, cid, lock=True)
        if row.calendar_approval_version != request.expected_version:
            raise ApiError(
                409, "calendar_permission_changed", "Refresh the approval setting and try again."
            )
        row.calendar_approval_mode = request.mode
        row.calendar_approval_version += 1
        row.calendar_approval_session_version = user.threadly_session_version
        return view(row, user)
