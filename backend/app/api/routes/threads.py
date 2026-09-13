"""Thread listing/detail — pure DB reads, no model calls (ADR 002). W1-live so
the frontend can integrate login -> sync -> list immediately."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.db import repositories as repo
from app.db.engine import get_session
from app.schemas.threads import ThreadListOut, ThreadOut

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_session)]

_PAGE_SIZE = 25


def _to_out(t) -> ThreadOut:
    return ThreadOut(
        thread_id=t.gmail_thread_id,
        version=t.version,
        subject=t.subject,
        last_msg_at=t.last_msg_at,
        needs_reply=t.needs_reply,  # NULL until the classifier ships (= "unclassified")
    )


@router.get("", response_model=ThreadListOut)
async def list_threads(
    user_id: CurrentUser, session: DB, filter: str = "all", page: int = 1
) -> ThreadListOut:
    if filter not in ("all", "needs_reply"):
        raise ApiError(422, "validation_error", "filter must be all|needs_reply")
    threads = await repo.list_threads(
        session,
        user_id,
        needs_reply=True if filter == "needs_reply" else None,
        page=max(page, 1),
        page_size=_PAGE_SIZE,
    )
    next_page = page + 1 if len(threads) == _PAGE_SIZE else None
    return ThreadListOut(threads=[_to_out(t) for t in threads], next_page=next_page)


@router.get("/{thread_id}")
async def get_thread(thread_id: str, user_id: CurrentUser, session: DB) -> dict:
    thread = await repo.get_thread_for_user(session, user_id, thread_id)
    if thread is None:
        raise ApiError(404, "not_found", "Unknown thread.")
    messages = await repo.thread_messages(session, thread.id)
    return {
        "thread": _to_out(thread).model_dump(),
        "messages": [
            {
                "gmail_msg_id": m.gmail_msg_id,
                "from_addr": m.from_addr,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "received_at": m.received_at.isoformat() if m.received_at else None,
                "reply_metadata": m.reply_metadata,
                "is_from_user": m.is_from_user,
                "body_clean": m.body_clean,
            }
            for m in messages
        ],
    }
