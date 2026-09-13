"""Module 3 — SYNC WORKER (W1, implemented).

initial_backfill: every page of the mailbox -> clean -> upsert, then store the
profile historyId as the incremental cursor.
incremental_sync: history.list from the stored cursor; a 404 means the cursor
expired and we fall back to a fresh backfill (the doc's rule).

Classification at ingest: hook below loads the AI team's classifier from
/ml/classifier when it ships; until then needs_reply stays NULL (UI treats it
as "unclassified", never as false).
"""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models import User
from app.sync import cleaning
from app.sync.gmail import GmailClient, GmailError, extract_body_text, header

log = logging.getLogger("threadly.sync")

_BACKFILL_QUERY = "-in:spam -in:trash"


@dataclass
class SyncReport:
    mode: str  # backfill | incremental
    messages_upserted: int
    threads_touched: int
    history_id: str | None


def classify_needs_reply(body_clean: str, from_addr: str | None) -> bool | None:
    """Hook for the AI team's BERT classifier (/ml/classifier). None until it ships."""
    return None


def _parse_sent_at(payload: dict, internal_ms: str | None) -> datetime | None:
    date_hdr = header(payload, "Date")
    if date_hdr:
        try:
            return parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            pass
    if internal_ms:
        try:
            return datetime.fromtimestamp(int(internal_ms) / 1000, tz=UTC)
        except (TypeError, ValueError):
            pass
    return None


async def _upsert_batch(
    session: AsyncSession, user: User, details: list[dict], user_email: str
) -> tuple[int, set[str]]:
    count = 0
    threads: set[str] = set()
    for msg in details:
        payload = msg.get("payload", {})
        gmail_thread_id = msg.get("threadId")
        if not gmail_thread_id:
            continue
        from_addr = header(payload, "From")
        sent_at = _parse_sent_at(payload, msg.get("internalDate"))
        body_clean = cleaning.clean_body(extract_body_text(payload))
        is_from_user = bool(from_addr and user_email and user_email.lower() in from_addr.lower())

        thread_pk = await repo.upsert_thread(
            session,
            user_id=user.id,
            gmail_thread_id=gmail_thread_id,
            subject=header(payload, "Subject"),
            last_msg_id=msg.get("id"),
            last_msg_at=sent_at,
        )
        await repo.upsert_message(
            session,
            user_id=user.id,
            thread_pk=thread_pk,
            gmail_msg_id=msg["id"],
            from_addr=from_addr,
            to_addrs=header(payload, "To"),
            sent_at=sent_at,
            is_from_user=is_from_user,
            body_clean=body_clean,
        )
        count += 1
        threads.add(gmail_thread_id)
    return count, threads


async def initial_backfill(
    session: AsyncSession,
    user: User,
    access_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncReport:
    client = GmailClient(access_token, transport=transport)
    refs = await client.list_all_message_ids(_BACKFILL_QUERY)  # ALL pages
    details = await client.get_messages_full([r["id"] for r in refs])
    count, threads = await _upsert_batch(session, user, details, user.email)

    profile = await client.get_profile()
    history_id = str(profile.get("historyId")) if profile.get("historyId") else None
    if history_id:
        await repo.set_history_id(session, user.id, history_id)
    user.last_synced_at = datetime.now(UTC)
    await session.commit()
    return SyncReport("backfill", count, len(threads), history_id)


async def incremental_sync(
    session: AsyncSession,
    user: User,
    access_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncReport:
    if not user.gmail_history_id:
        return await initial_backfill(session, user, access_token, transport=transport)
    client = GmailClient(access_token, transport=transport)
    try:
        changed, newest = await client.history_since(user.gmail_history_id)
    except GmailError as exc:
        if exc.status == 404:  # expired cursor -> full backfill (doc rule)
            log.info("history cursor expired for user %s — backfilling", user.id)
            return await initial_backfill(session, user, access_token, transport=transport)
        raise

    details = await client.get_messages_full(changed) if changed else []
    count, threads = await _upsert_batch(session, user, details, user.email)
    if newest:
        await repo.set_history_id(session, user.id, newest)
    user.last_synced_at = datetime.now(UTC)
    await session.commit()
    return SyncReport("incremental", count, len(threads), newest or user.gmail_history_id)
