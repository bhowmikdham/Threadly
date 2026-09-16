"""Data-layer repositories (W1). All writes are UPSERTS against the uniqueness
rules in docs/data-model.md — sync must be safely re-runnable at any time."""

from datetime import UTC, datetime

from sqlalchemy import String, all_, any_, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import ARRAY
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
    google_scopes: list[str] | None = None,
    google_identity: dict | None = None,
    email_verified: bool | None = None,
    google_connected: bool | None = None,
    now: datetime | None = None,
) -> User:
    """Insert on first login; on later logins update identity + access token,
    and only touch refresh_token when Google actually returned a new one."""
    now = now or datetime.now(UTC)
    if google_connected is None:
        google_connected = bool(access_token_enc or refresh_token_enc)
    existing = (
        await session.execute(
            select(User).where(User.google_sub == google_sub).with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if existing is None:
        inserted = await session.scalar(
            pg_insert(User).values(
                google_sub=google_sub, email=email, display_name=display_name,
                access_token_enc=access_token_enc,
                access_token_expires_at=access_token_expires_at,
                refresh_token_enc=refresh_token_enc, google_scopes=google_scopes,
                google_identity=google_identity, google_email_verified=email_verified,
                google_connected=google_connected, google_connected_at=now,
            ).on_conflict_do_nothing(index_elements=[User.google_sub]).returning(User.id)
        )
        if inserted is not None:
            return await session.get(User, inserted)
        existing = await session.scalar(
            select(User).where(User.google_sub == google_sub).with_for_update()
            .execution_options(populate_existing=True)
        )
    existing.email = email
    existing.display_name = display_name
    existing.access_token_enc = access_token_enc
    existing.access_token_expires_at = access_token_expires_at
    if refresh_token_enc is not None:
        existing.refresh_token_enc = refresh_token_enc
    existing.google_scopes = google_scopes
    existing.google_identity = google_identity
    existing.google_email_verified = email_verified
    existing.google_connected = google_connected
    existing.google_connected_at = now
    existing.google_account_version += 1
    existing.google_token_version += 1
    await session.flush()
    return existing


# ---------------------------------------------------------------- threads / messages


def message_order(*, newest_first: bool = False):
    """Provider receipt time, legacy Date fallback, then a locale-independent ID tie-break."""
    timestamp = func.coalesce(Message.received_at, Message.sent_at)
    mid = Message.gmail_msg_id.collate("C")
    if newest_first:
        return timestamp.desc().nulls_last(), mid.desc()
    return timestamp.asc().nulls_first(), mid.asc()


async def apply_mailbox_changes(
    session: AsyncSession,
    user_id: int,
    messages: list[dict],
    removed: set[str],
    *,
    full: bool = False,
) -> tuple[int, int]:
    """Caller holds the user's sync fence. Change detection makes replays idempotent."""
    touched: set[int] = set()
    incoming = {m["gmail_msg_id"] for m in messages}
    removal = select(Message).where(Message.user_id == user_id)
    if full:
        removal = removal.where(Message.gmail_msg_id != all_(cast(list(incoming), ARRAY(String))))
    else:
        removal = removal.where(Message.gmail_msg_id == any_(cast(list(removed), ARRAY(String))))
    for old in (await session.execute(removal)).scalars():
        touched.add(old.thread_id)
        await session.delete(old)

    for values in messages:
        values = dict(values)
        gmail_thread_id = values.pop("gmail_thread_id")
        # A no-op on conflict, so ingestion order cannot rewrite the thread head.
        await session.execute(
            pg_insert(Thread)
            .values(
                user_id=user_id,
                gmail_thread_id=gmail_thread_id,
            )
            .on_conflict_do_nothing(index_elements=[Thread.user_id, Thread.gmail_thread_id])
        )
        thread_pk = (
            await session.execute(
                select(Thread.id).where(
                    Thread.user_id == user_id,
                    Thread.gmail_thread_id == gmail_thread_id,
                )
            )
        ).scalar_one()
        values.update(user_id=user_id, thread_id=thread_pk)
        existing = (
            await session.execute(
                select(Message).where(
                    Message.user_id == user_id,
                    Message.gmail_msg_id == values["gmail_msg_id"],
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Message(**values))
            touched.add(thread_pk)
        elif any(getattr(existing, key) != value for key, value in values.items()):
            touched.update((existing.thread_id, thread_pk))
            for key, value in values.items():
                setattr(existing, key, value)
    await session.flush()
    for thread_pk in sorted(touched):
        latest = (
            await session.execute(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.thread_id == thread_pk,
                )
                .order_by(*message_order(newest_first=True))
                .limit(1)
            )
        ).scalar_one_or_none()
        await session.execute(
            update(Thread)
            .where(
                Thread.user_id == user_id,
                Thread.id == thread_pk,
            )
            .values(
                last_msg_id=latest.gmail_msg_id if latest else None,
                last_msg_at=(latest.received_at or latest.sent_at) if latest else None,
                subject=latest.subject if latest else None,
                version=Thread.version + 1,
                needs_reply=None,
            )
        )
        await session.execute(delete(Summary).where(Summary.thread_id == thread_pk))
    # Keep empty thread rows: saved snapshots/tasks reference them and remain immutable.
    return len(messages), len(touched)


async def list_threads(
    session: AsyncSession, user_id: int, *, needs_reply: bool | None, page: int, page_size: int
) -> list[Thread]:
    q = (
        select(Thread)
        .where(Thread.user_id == user_id)
        .order_by(Thread.last_msg_at.desc().nulls_last(), Thread.id.desc())
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
                select(Message).where(Message.thread_id == thread_pk).order_by(*message_order())
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
    expected_version: int,
) -> bool:
    # Serialize publication with sync's thread update, then compare the source version.
    current = (
        await session.execute(
            select(Thread.version)
            .where(
                Thread.id == thread_pk,
                Thread.user_id == user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current != expected_version:
        return False
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
    return True
