"""The triage surface: inbound mail filtered by the AI team's per-email
labels. `GET /v1/messages?priority=high` is the priority inbox."""
import json

from fastapi import APIRouter, Depends, Query

from .. import db, security

router = APIRouter()


@router.get("/messages")
async def list_messages(
    user_id: int = Depends(security.current_user),
    priority: str | None = Query(default=None),
    action: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
):
    conditions = ["m.user_id = $1", "m.is_sent = FALSE"]
    args: list = [user_id]
    for column, value in (("priority", priority), ("action", action), ("category", category)):
        if value:
            args.append(value)
            conditions.append(f"m.{column} = ${len(args)}")
    args.extend([limit, offset])

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT m.id, m.thread_id, t.subject AS thread_subject, m.subject, m.from_addr,
                   m.sent_at, m.snippet, m.priority, m.action, m.category, m.labels
            FROM messages m JOIN threads t ON t.id = m.thread_id
            WHERE {' AND '.join(conditions)}
            ORDER BY m.sent_at DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """,
            *args,
        )
    items = [dict(r) for r in rows]
    for item in items:  # asyncpg returns jsonb as a string
        if isinstance(item.get("labels"), str):
            item["labels"] = json.loads(item["labels"])
    return {"items": items, "limit": limit, "offset": offset}
