"""Capture bounded immutable copies of authorized synced message excerpts."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.db.models import ContextSnapshot, Message, Thread

CHAR_BUDGET = 12000
MESSAGE_LIMIT = 50


async def capture_thread(
    session: AsyncSession, user_id: int, gmail_thread_id: str
) -> ContextSnapshot:
    # One statement gives a consistent view even if sync commits concurrently.
    rows = (
        await session.execute(
            select(
                Thread.id.label("thread_pk"),
                Message.gmail_msg_id,
                Message.from_addr,
                Message.sent_at,
                func.substr(func.coalesce(Message.body_clean, ""), 1, CHAR_BUDGET).label("body"),
                func.length(func.coalesce(Message.body_clean, "")).label("body_length"),
                func.count().over().label("total"),
            )
            .join(Message, Message.thread_id == Thread.id)
            .where(
                Thread.user_id == user_id,
                Message.user_id == user_id,
                Thread.gmail_thread_id == gmail_thread_id,
            )
            .order_by(
                Message.sent_at.desc().nulls_first(), Message.gmail_msg_id.collate("C").desc()
            )
            .limit(MESSAGE_LIMIT)
        )
    ).all()
    if not rows:
        raise ApiError(404, "context_not_found", "No accessible synced messages for this thread.")
    remaining, truncated = CHAR_BUDGET, 0
    messages = []
    for row in rows:
        if remaining <= 0:
            break
        body = row.body[:remaining]
        truncated += int(len(body) < row.body_length)
        messages.append(
            {
                "message_id": row.gmail_msg_id,
                "from_addr": row.from_addr,
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                "body": body,
            }
        )
        remaining -= len(body)
    messages.reverse()
    if not any(m["body"].strip() for m in messages):
        raise ApiError(409, "context_empty", "The synced messages contain no usable text.")
    payload = {
        "schema_version": "1.0",
        "scope": "synced_thread_excerpts",
        "thread_id": gmail_thread_id,
        "messages": messages,
        "total_synced_messages": rows[0].total,
        "omitted_messages": rows[0].total - len(messages),
        "truncated_messages": truncated,
    }
    snapshot = ContextSnapshot(
        id=str(uuid4()),
        user_id=user_id,
        thread_id=rows[0].thread_pk,
        source_hash=digest(payload),
        payload=payload,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot
