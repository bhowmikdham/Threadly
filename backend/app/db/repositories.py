"""Data-layer repositories (W1). All writes are UPSERTS against the uniqueness
rules in docs/data-model.md — sync must be safely re-runnable at any time."""
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, Summary, Thread, User

# ---------------------------------------------------------------- users


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def upsert_user(
    session: AsyncSession,
    *,
    google_sub: str,
    email: str,
    display_name: str | None,
    access_token_enc: bytes,
    access_token_expires_at: datetime,
    refresh_token_enc: bytes | None,
) -> User:
    """Insert on first login; on later logins update identity + access token,
    and only touch refresh_token when Google actually returned a new one."""
    existing = (
        await session.execute(select(User).where(User.google_sub == google_sub))
    ).scalar_one_or_none()
    if existing is None:
        user = User(
            google_sub=google_sub,
            email=email,
            display_name=display_name,
            access_token_enc=access_token_enc,
            access_token_expires_at=access_token_expires_at,
            refresh_token_enc=refresh_token_enc,
        )
        session.add(user)
        await session.flush()
        return user
    existing.email = email
    existing.display_name = display_name
    existing.access_token_enc = access_token_enc
    existing.access_token_expires_at = access_token_expires_at
    if refresh_token_enc is not None:
        existing.refresh_token_enc = refresh_token_enc
    await session.flush()
    return existing


async def set_history_id(session: AsyncSession, user_id: int, history_id: str) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(gmail_history_id=history_id)
    )


# ---------------------------------------------------------------- threads / messages


async def upsert_thread(
    session: AsyncSession,
    *,
    user_id: int,
    gmail_thread_id: str,
    subject: str | None,
    last_msg_id: str | None,
    last_msg_at: datetime | None,
) -> int:
    """ON CONFLICT (user_id, gmail_thread_id) — keeps last_msg_* fresh. Returns thread pk."""
    stmt = (
        pg_insert(Thread)
        .values(
            user_id=user_id,
            gmail_thread_id=gmail_thread_id,
            subject=subject,
            last_msg_id=last_msg_id,
            last_msg_at=last_msg_at,
        )
        .on_conflict_do_update(
            index_elements=[Thread.user_id, Thread.gmail_thread_id],
            set_={"subject": subject, "last_msg_id": last_msg_id, "last_msg_at": last_msg_at},
        )
        .returning(Thread.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def upsert_message(
    session: AsyncSession,
    *,
    user_id: int,
    thread_pk: int,
    gmail_msg_id: str,
    from_addr: str | None,
    to_addrs: str | None,
    sent_at: datetime | None,
    is_from_user: bool,
    body_clean: str | None,
) -> None:
    stmt = (
        pg_insert(Message)
        .values(
            user_id=user_id,
            thread_id=thread_pk,
            gmail_msg_id=gmail_msg_id,
            from_addr=from_addr,
            to_addrs=to_addrs,
            sent_at=sent_at,
            is_from_user=is_from_user,
            body_clean=body_clean,
        )
        .on_conflict_do_update(
            index_elements=[Message.user_id, Message.gmail_msg_id],
            set_={"body_clean": body_clean, "is_from_user": is_from_user},
        )
    )
    await session.execute(stmt)


async def list_threads(
    session: AsyncSession, user_id: int, *, needs_reply: bool | None, page: int, page_size: int
) -> list[Thread]:
    q = (
        select(Thread)
        .where(Thread.user_id == user_id)
        .order_by(Thread.last_msg_at.desc().nulls_last())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if needs_reply is not None:
        q = q.where(Thread.needs_reply.is_(needs_reply))
    return list((await session.execute(q)).scalars())


async def get_thread_for_user(
    session: AsyncSession, user_id: int, gmail_thread_id: str
) -> Thread | None:
    return (
        await session.execute(
            select(Thread).where(
                Thread.user_id == user_id, Thread.gmail_thread_id == gmail_thread_id
            )
        )
    ).scalar_one_or_none()


async def thread_messages(session: AsyncSession, thread_pk: int) -> list[Message]:
    return list(
        (
            await session.execute(
                select(Message)
                .where(Message.thread_id == thread_pk)
                .order_by(Message.sent_at.asc().nulls_last())
            )
        ).scalars()
    )


# ---------------------------------------------------------------- summaries (the cache)


async def get_cached_summary(
    session: AsyncSession, thread_pk: int, last_msg_id: str
) -> Summary | None:
    """THE cache: (thread_id, last_msg_id). New message => new key => miss."""
    return (
        await session.execute(
            select(Summary).where(
                Summary.thread_id == thread_pk, Summary.last_msg_id == last_msg_id
            )
        )
    ).scalar_one_or_none()


async def store_summary(
    session: AsyncSession,
    *,
    user_id: int,
    thread_pk: int,
    last_msg_id: str,
    body: str,
    model_used: str | None,
) -> None:
    stmt = (
        pg_insert(Summary)
        .values(
            user_id=user_id,
            thread_id=thread_pk,
            last_msg_id=last_msg_id,
            body=body,
            model_used=model_used,
        )
        .on_conflict_do_update(
            index_elements=[Summary.thread_id, Summary.last_msg_id],
            set_={"body": body, "model_used": model_used},
        )
    )
    await session.execute(stmt)
