"""Bounded live Gmail listing/detail; legacy DB reads only in explicit compatibility mode."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
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


@router.get("", response_model=None)
async def list_threads(
    user_id: CurrentUser,
    session: DB,
    response: Response,
    filter: str = "all",
    page: int = 1,
    cursor: str | None = Query(default=None, max_length=4000),
    days: int = Query(default=7, ge=1, le=30),
) -> ThreadListOut:
    from app.config import get_settings

    if get_settings().gmail_source_mode == "on_demand":
        from datetime import UTC, datetime, timedelta

        from app.assistant.summary import digest
        from app.mail.search import page as live_page

        response.headers["Cache-Control"] = "no-store"
        if filter != "all" or page != 1:
            raise ApiError(
                422,
                "live_listing_filter",
                "Use all with a cursor; local classification is unavailable.",
            )
        # Stable UTC calendar-day boundary permits cursors within the same day.
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=days
        )
        query = f"after:{int(start.timestamp())} -in:spam -in:trash"
        rows, next_cursor = await live_page(user_id, query, digest(query), cursor)
        seen, result = set(), []
        for row in rows:
            tid = row["gmail_thread_id"]
            if tid not in seen:
                seen.add(tid)
                result.append(
                    {
                        "thread_id": tid,
                        "subject": row["subject"],
                        "last_msg_at": row["received_at"],
                        "needs_reply": None,
                        "version": 0,
                    }
                )
        return {
            "threads": result,
            "next_page": None,
            "next_cursor": next_cursor,
            "source": "live_gmail",
            "days": days,
            "persisted_email_content": False,
        }
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
async def get_thread(thread_id: str, user_id: CurrentUser, session: DB, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    from app.config import get_settings

    if get_settings().gmail_source_mode == "on_demand":
        from sqlalchemy import select

        from app.db.models import ContextSnapshot, Thread
        from app.mail import live

        source = await live.thread(user_id, thread_id)
        thread = await session.scalar(
            select(Thread).where(Thread.user_id == user_id, Thread.gmail_thread_id == thread_id)
        )
        previous = (
            await session.scalar(
                select(ContextSnapshot)
                .where(ContextSnapshot.user_id == user_id, ContextSnapshot.thread_id == thread.id)
                .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.id.desc())
                .limit(1)
            )
            if thread
            else None
        )
        version = thread.version if thread else 1
        if previous and (
            previous.payload.get("fingerprint") != source["fingerprint"]
            or previous.payload.get("account_version") != source["account_version"]
        ):
            version += 1
        latest = source["messages"][-1]
        return {
            "thread": {
                "thread_id": thread_id,
                "subject": latest["subject"],
                "last_msg_at": latest["received_at"],
                "needs_reply": None,
                "version": version,
            },
            "messages": source["messages"],
            "source": "live_gmail",
            "persisted_email_content": False,
        }
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
