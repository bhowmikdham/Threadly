from fastapi import APIRouter, Depends, Query
from threadly_common.errors import APIError

from .. import db, security

router = APIRouter()


@router.get("/threads")
async def list_threads(
    user_id: int = Depends(security.current_user),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
):
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, gmail_thread_id, subject, last_msg_at, msg_count
            FROM threads WHERE user_id = $1
            ORDER BY last_msg_at DESC NULLS LAST
            LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: int, user_id: int = Depends(security.current_user)):
    async with db.pool().acquire() as conn:
        thread = await conn.fetchrow(
            "SELECT id, gmail_thread_id, subject, last_msg_at, msg_count FROM threads WHERE id = $1 AND user_id = $2",
            thread_id, user_id,
        )
        if not thread:
            raise APIError("not_found", "Thread not found.", status=404)
        messages = await conn.fetch(
            """
            SELECT id, gmail_msg_id, from_addr, to_addrs, is_sent, sent_at, subject, snippet, body_text
            FROM messages WHERE thread_id = $1 AND user_id = $2
            ORDER BY sent_at ASC
            """,
            thread_id, user_id,
        )
    return {**dict(thread), "messages": [dict(m) for m in messages]}
