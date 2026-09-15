"""Versioned thread-view capture, independent of chronological message ordering."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.assistant.context import CHAR_BUDGET
from app.assistant.summary import digest
from app.db.models import ContextSnapshot, Message, Thread
from app.db.repositories import message_order
from app.schemas.ui_context import UIContextSnapshotRequest

CAPTURE_POLICY = "ui-message-map-1.0:50-ids:12000-chars:equal-body-budget:15m-age:5m-future"


async def capture_view(
    session: AsyncSession, user_id: int, request: UIContextSnapshotRequest
) -> ContextSnapshot:
    mapping = request.ui_map
    captured = datetime.fromisoformat(mapping.captured_at)
    now = datetime.now(UTC)
    if captured < now - timedelta(minutes=15) or captured > now + timedelta(minutes=5):
        raise ApiError(409, "ui_capture_expired", "Capture the current view again.")
    per_message = CHAR_BUDGET // len(mapping.visible_message_ids)
    # Count and source/version hydration share one statement/snapshot during sync.
    total = (
        select(func.count(Message.id))
        .where(Message.thread_id == Thread.id, Message.user_id == user_id)
        .correlate(Thread)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                Thread.id.label("thread_pk"),
                Thread.version.label("thread_version"),
                total.label("total"),
                Message.gmail_msg_id,
                Message.from_addr,
                Message.sent_at,
                func.substr(func.coalesce(Message.body_clean, ""), 1, per_message).label("body"),
                func.length(func.coalesce(Message.body_clean, "")).label("body_length"),
            )
            .join(Message, Message.thread_id == Thread.id)
            .where(
                Thread.user_id == user_id,
                Message.user_id == user_id,
                Thread.gmail_thread_id == request.thread_id,
                Message.gmail_msg_id.in_(mapping.visible_message_ids),
            )
            .order_by(*message_order())
        )
    ).all()
    if {row.gmail_msg_id for row in rows} != set(mapping.visible_message_ids):
        raise ApiError(404, "ui_reference_not_found", "A visible message is not accessible.")
    if rows[0].thread_version != mapping.thread_version:
        raise ApiError(409, "ui_context_changed", "The synced thread changed; capture it again.")
    messages = [
        {
            "message_id": row.gmail_msg_id,
            "from_addr": row.from_addr,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "body": row.body,
        }
        for row in rows
    ]
    if not any(m["body"].strip() for m in messages):
        raise ApiError(409, "context_empty", "The visible messages contain no usable synced text.")
    truncated_ids = [row.gmail_msg_id for row in rows if len(row.body) < row.body_length]
    payload = {
        "schema_version": "1.1",
        "scope": "synced_ui_message_excerpts",
        "thread_id": request.thread_id,
        "thread_version": rows[0].thread_version,
        "messages": messages,
        "total_synced_messages": rows[0].total,
        "omitted_messages": rows[0].total - len(messages),
        "truncated_messages": len(truncated_ids),
        "truncated_message_ids": truncated_ids,
        "ui_map": mapping.model_dump(),
        "capture_policy": CAPTURE_POLICY,
    }
    result = ContextSnapshot(
        id=str(uuid4()),
        user_id=user_id,
        thread_id=rows[0].thread_pk,
        source_hash=digest(payload),
        payload=payload,
    )
    session.add(result)
    await session.flush()
    return result
