"""Fetch outside DB transactions, then atomically reconcile behind a per-user fence.

Backfill captures a starting history cursor BEFORE listing, replays changes after
listing, and commits that replay's cursor with the messages. Concurrent fetches
may overlap; only one can commit for the observed sync_version.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models import User
from app.sync import cleaning
from app.sync.gmail import GmailClient, GmailError, extract_body_text, header

_BACKFILL_QUERY = "-in:spam -in:trash"
_REPLY_HEADERS = ("From", "To", "Cc", "Reply-To", "Message-ID", "In-Reply-To", "References", "Date")


class SyncConflict(Exception):
    """A newer sync committed while this one was fetching; retry from current state."""


@dataclass
class SyncReport:
    mode: str
    messages_upserted: int
    threads_touched: int
    history_id: str | None


def classify_needs_reply(body_clean: str, from_addr: str | None) -> bool | None:
    """Reserved for the AI team's classifier. NULL means unclassified."""
    return None


def _received_at(internal_ms: str | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(internal_ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_sent_at(payload: dict, internal_ms: str | None) -> datetime | None:
    try:
        value = parsedate_to_datetime(header(payload, "Date"))
        # Do not interpret an unzoned Date in the server's local timezone.
        if value.tzinfo is not None:
            return value.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        pass
    return _received_at(internal_ms)


def _addresses(values: list[str]) -> list[str]:
    try:
        return sorted(
            {
                address.casefold()
                for _, address in getaddresses(values)
                if address.count("@") == 1 and not any(c.isspace() for c in address)
            }
        )
    except (TypeError, ValueError):
        return []


def _message_values(msg: dict, user_email: str) -> dict:
    payload = msg.get("payload", {})
    # Preserve duplicates instead of silently trusting the first conflicting header.
    headers = {
        name.lower(): [
            h["value"]
            for h in payload.get("headers", [])
            if h.get("name", "").casefold() == name.casefold() and isinstance(h.get("value"), str)
        ]
        for name in _REPLY_HEADERS
    }
    addresses = {name: _addresses(headers[name]) for name in ("from", "to", "cc", "reply-to")}
    from_addr = header(payload, "From")
    return {
        "gmail_msg_id": msg["id"],
        "gmail_thread_id": msg["threadId"],
        # The legacy column is bounded; full original From values live in reply_metadata.
        "from_addr": from_addr[:320] if from_addr else None,
        "to_addrs": header(payload, "To"),
        "subject": header(payload, "Subject"),
        "sent_at": _parse_sent_at(payload, msg.get("internalDate")),
        "received_at": _received_at(msg.get("internalDate")),
        "is_from_user": len(headers["from"]) == 1 and addresses["from"] == [user_email.casefold()],
        "body_clean": cleaning.clean_body(extract_body_text(payload)),
        "reply_metadata": {
            "schema_version": "1.0",
            "headers": headers,
            "addresses": addresses,
            "label_ids": sorted(set(msg.get("labelIds", []))),
        },
    }


async def _backfill(client: GmailClient) -> tuple[list[dict], str]:
    profile = await client.get_profile()
    start = str(profile.get("historyId", ""))
    if not start.isdigit():
        raise GmailError("missing gmail starting cursor")
    refs = await client.list_all_message_ids(_BACKFILL_QUERY)
    details = {
        m["id"]: m
        for m in await client.get_messages_full(
            [r["id"] for r in refs],
            ignore_missing=True,
        )
    }
    changed, newest = await client.history_since(start)
    # Refetch even IDs already seen: their labels or existence may have changed.
    replay = await client.get_messages_full(changed, ignore_missing=True)
    for mid in changed:
        details.pop(mid, None)
    details.update({m["id"]: m for m in replay})
    return list(details.values()), newest


async def _sync(
    session: AsyncSession,
    user: User,
    access_token: str,
    *,
    full: bool,
    transport: httpx.AsyncBaseTransport | None,
) -> SyncReport:
    user_id, email, cursor, version = user.id, user.email, user.gmail_history_id, user.sync_version
    # This API owns the session's unit of work; no pending unrelated mutations are accepted.
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("sync requires a clean session")
    await session.rollback()  # Release the API's authentication read transaction.
    client = GmailClient(access_token, transport=transport)
    full = full or not cursor
    changed: list[str] = []
    if not full:
        try:
            changed, newest = await client.history_since(cursor)
        except GmailError as exc:
            if exc.status != 404:
                raise
            full = True
    if full:
        # A replay 404 propagates; next invocation restarts, never advances a cursor with a gap.
        details, newest = await _backfill(client)
    else:
        details = await client.get_messages_full(changed, ignore_missing=True)
    details = [m for m in details if not {"SPAM", "TRASH"}.intersection(m.get("labelIds", []))]
    values = [_message_values(m, email) for m in details]
    removed = set(changed) - {m["id"] for m in details}
    try:
        current = (
            await session.execute(
                select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if current is None or current.sync_version != version:
            raise SyncConflict()
        count, touched = await repo.apply_mailbox_changes(
            session, user_id, values, removed, full=full
        )
        current.gmail_history_id = newest
        current.sync_version += 1
        current.last_synced_at = datetime.now(UTC)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return SyncReport("backfill" if full else "incremental", count, touched, newest)


async def initial_backfill(
    session: AsyncSession,
    user: User,
    access_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncReport:
    return await _sync(session, user, access_token, full=True, transport=transport)


async def incremental_sync(
    session: AsyncSession,
    user: User,
    access_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncReport:
    return await _sync(session, user, access_token, full=False, transport=transport)
