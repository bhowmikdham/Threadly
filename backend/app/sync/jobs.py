"""Restartable page reads into staging; only complete replay publishes mailbox changes.

Leases protect checkpoints. User/account/sync versions fence the final transaction.
No provider network call occurs while holding a database transaction.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.auth.service import get_valid_access_token
from app.capabilities.service import build_capabilities
from app.config import get_settings
from app.db import repositories as repo
from app.db.engine import get_session_factory
from app.db.models import MailSyncJob, MailSyncStage, Message, User
from app.sync.gmail import BASE, GmailClient, GmailError
from app.sync.worker import _message_values


async def submit(session, owner, request_id):
    user = await session.get(User, owner, with_for_update=True, populate_existing=True)
    if user is None:
        raise ApiError(401, "unauthorized", "Unknown account.")
    old = await session.scalar(
        select(MailSyncJob).where(
            MailSyncJob.user_id == owner, MailSyncJob.request_id == request_id
        )
    )
    if old:
        return old
    if (
        get_settings().gmail_source_mode == "on_demand"
        or not get_settings().mailbox_background_sync_enabled
    ):
        raise ApiError(503, "sync_disabled", "Background sync is paused.")
    if not next(c for c in build_capabilities(user)["capabilities"] if c["id"] == "gmail_read")[
        "ready"
    ]:
        raise ApiError(409, "gmail_reconnect_required", "Connect Gmail read access first.")
    active = await session.scalar(
        select(MailSyncJob).where(
            MailSyncJob.user_id == owner, MailSyncJob.state.in_(["queued", "running"])
        )
    )
    if active:
        raise ApiError(
            409, "sync_in_progress", "A mailbox sync is already active.", {"job_id": active.id}
        )
    job = MailSyncJob(
        id=str(uuid4()),
        user_id=owner,
        request_id=request_id,
        account_version=user.google_account_version,
        sync_version=user.sync_version,
        full=not bool(user.gmail_history_id),
        state="queued",
        phase="start",
        cursor={"start_history": user.gmail_history_id},
        attempts=0,
        resets=0,
    )
    session.add(job)
    await session.flush()
    return job


def view(job):
    return {
        "job_id": job.id,
        "state": job.state,
        "phase": job.phase,
        "complete": job.state == "succeeded",
        "pages_read": job.cursor.get("pages", 0),
        "mode": "backfill" if job.full else "incremental",
        "error_code": job.error_code,
        "result": job.result,
    }


async def owned(session, owner, identifier):
    row = await session.scalar(
        select(MailSyncJob).where(MailSyncJob.id == identifier, MailSyncJob.user_id == owner)
    )
    if row is None:
        raise ApiError(404, "not_found", "Unknown mailbox sync.")
    return row


async def claim(factory):
    if (
        get_settings().gmail_source_mode == "on_demand"
        or not get_settings().mailbox_background_sync_enabled
    ):
        return None
    async with factory.begin() as session:
        now = func.clock_timestamp()
        row = await session.scalar(
            select(MailSyncJob)
            .where(
                or_(
                    and_(MailSyncJob.state == "queued", MailSyncJob.available_at <= now),
                    and_(MailSyncJob.state == "running", MailSyncJob.lease_expires_at <= now),
                )
            )
            .order_by(MailSyncJob.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        if row.attempts >= 5:
            row.state, row.error_code = "failed", "sync_retry_budget_exhausted"
            row.lease_token, row.lease_expires_at = None, None
            return None
        row.state, row.lease_token = "running", str(uuid4())
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=180
        )
        row.attempts += 1
        return row


async def fetch(job, token, transport):
    client = GmailClient(token, transport=transport)
    cursor = dict(job.cursor)
    if job.phase == "start":
        if job.full:
            history = str((await client.get_profile()).get("historyId", ""))
            if not history.isdigit():
                raise GmailError("Invalid starting history")
            cursor = {"start_history": history, "pages": 0, "seen_pages": []}
        else:
            cursor.update(pages=0, seen_pages=[])
        return "listing" if job.full else "history", cursor, []
    params = {"maxResults": 100}
    if cursor.get("page_token"):
        params["pageToken"] = cursor["page_token"]
    if job.phase == "listing":
        params["q"] = "-in:spam -in:trash"
        path = "/messages"
    else:
        params["startHistoryId"] = cursor["start_history"]
        path = "/history"
    async with client._client() as http:
        body = await client._get(http, BASE + path, params)
    ids = []
    if job.phase == "listing":
        refs = body.get("messages", [])
        if not isinstance(refs, list) or len(refs) > 100:
            raise GmailError("Invalid message page")
        ids = [m["id"] for m in refs]
    else:
        history = body.get("history", [])
        if not isinstance(history, list) or len(history) > 100:
            raise GmailError("Invalid history page")
        for record in history:
            for kind in ("messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
                ids.extend(item["message"]["id"] for item in record.get(kind, []))
        newest = str(body.get("historyId", ""))
        if not newest.isdigit() or int(newest) < int(cursor["start_history"]):
            raise GmailError("Invalid history checkpoint")
        cursor["latest_history"] = newest
    ids = list(dict.fromkeys(ids))
    if len(ids) > 1000 or any(
        not isinstance(mid, str) or not 0 < len(mid) <= 32 or not mid.isalnum() for mid in ids
    ):
        raise GmailError("Message page exceeds supported bounds")
    rows = await client.get_messages_full(ids, ignore_missing=True)
    by_id = {m["id"]: m for m in rows if not set(m.get("labelIds", [])) & {"SPAM", "TRASH"}}
    changes = [(mid, by_id.get(mid)) for mid in ids]
    next_page = body.get("nextPageToken")
    pages = cursor.get("seen_pages", [])
    if next_page is not None and (
        not isinstance(next_page, str) or not 0 < len(next_page) <= 4096 or next_page in pages
    ):
        raise GmailError("Invalid repeated page token")
    cursor["pages"] = cursor.get("pages", 0) + 1
    if cursor["pages"] > 1000:
        raise GmailError("Mailbox exceeds supported page budget")
    cursor["page_token"] = next_page
    cursor["seen_pages"] = pages + ([next_page] if next_page else [])
    if next_page:
        return job.phase, cursor, changes
    cursor["seen_pages"] = []
    return "history" if job.phase == "listing" else "publish", cursor, changes


async def fenced(session, claim):
    # Auth/sync fence precedes job; no path locks a job then asks for its user.
    user = await session.get(User, claim.user_id, with_for_update=True, populate_existing=True)
    row = await session.get(MailSyncJob, claim.id, with_for_update=True, populate_existing=True)
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        row is None
        or row.state != "running"
        or row.lease_token != claim.lease_token
        or row.lease_expires_at <= now
    ):
        return None, None
    if (
        user is None
        or not user.google_connected
        or user.google_account_version != row.account_version
        or user.sync_version != row.sync_version
    ):
        row.state, row.error_code, row.lease_token, row.lease_expires_at = (
            "failed",
            "sync_source_changed",
            None,
            None,
        )
        return None, None
    return user, row


async def checkpoint(factory, claim, phase, cursor, changes):
    async with factory.begin() as session:
        user, row = await fenced(session, claim)
        if row is None:
            return
        for mid, message in changes:
            payload = _message_values(message, user.email) if message else None
            if payload:
                payload = json.loads(json.dumps(payload, default=lambda d: d.isoformat()))
            await session.execute(
                insert(MailSyncStage)
                .values(job_id=row.id, user_id=row.user_id, message_id=mid, payload=payload)
                .on_conflict_do_update(
                    index_elements=["job_id", "message_id"], set_={"payload": payload}
                )
            )
        count = await session.scalar(
            select(func.count()).select_from(MailSyncStage).where(MailSyncStage.job_id == row.id)
        )
        if count > get_settings().mailbox_sync_max_messages:
            row.state, row.error_code = "failed", "mailbox_sync_size_limit"
        else:
            row.state, row.phase, row.cursor, row.attempts = "queued", phase, cursor, 0
        row.lease_token, row.lease_expires_at = None, None


async def publish(factory, claim):
    async with factory.begin() as session:
        user, row = await fenced(session, claim)
        if row is None:
            return
        latest = row.cursor.get("latest_history")
        if not latest or not latest.isdigit():
            raise GmailError("Missing final history checkpoint")
        key, count, touched = "", 0, 0
        while True:
            batch = (
                await session.scalars(
                    select(MailSyncStage)
                    .where(MailSyncStage.job_id == row.id, MailSyncStage.message_id > key)
                    .order_by(MailSyncStage.message_id)
                    .limit(100)
                )
            ).all()
            if not batch:
                break
            values, removed = [], set()
            for stage in batch:
                if stage.payload is None:
                    removed.add(stage.message_id)
                else:
                    payload = dict(stage.payload)
                    for field in ("sent_at", "received_at"):
                        if payload[field]:
                            payload[field] = datetime.fromisoformat(payload[field])
                    values.append(payload)
            n, t = await repo.apply_mailbox_changes(session, row.user_id, values, removed)
            count, touched = count + n, touched + t
            key = batch[-1].message_id
        if row.full:
            present = select(MailSyncStage.message_id).where(
                MailSyncStage.job_id == row.id, MailSyncStage.payload.is_not(None)
            )
            while True:
                missing = (
                    await session.scalars(
                        select(Message.gmail_msg_id)
                        .where(Message.user_id == row.user_id, Message.gmail_msg_id.not_in(present))
                        .limit(100)
                    )
                ).all()
                if not missing:
                    break
                _, t = await repo.apply_mailbox_changes(session, row.user_id, [], set(missing))
                touched += t
        if row.lease_expires_at <= await session.scalar(select(func.clock_timestamp())):
            raise ApiError(
                409, "sync_publish_lease_expired", "Sync publication needs a fresh lease."
            )
        user.gmail_history_id, user.sync_version = latest, user.sync_version + 1
        user.last_synced_at = await session.scalar(select(func.clock_timestamp()))
        row.state, row.result = (
            "succeeded",
            {"messages_upserted": count, "thread_update_operations": touched},
        )
        row.lease_token, row.lease_expires_at, row.error_code = None, None, None
        await session.execute(delete(MailSyncStage).where(MailSyncStage.job_id == row.id))


async def failure(factory, claim, error):
    async with factory.begin() as session:
        _, row = await fenced(session, claim)
        if row is None:
            return
        if (
            isinstance(error, GmailError)
            and error.status == 404
            and row.phase == "history"
            and row.resets < 2
        ):
            row.full, row.phase, row.cursor, row.resets = True, "start", {}, row.resets + 1
            row.attempts = 0
            await session.execute(delete(MailSyncStage).where(MailSyncStage.job_id == row.id))
        row.state = "failed" if row.attempts >= 5 else "queued"
        row.error_code = "mailbox_sync_unavailable"
        row.available_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=30
        )
        row.lease_token, row.lease_expires_at = None, None


async def run_once(factory=None, *, transport=None, token_loader=None):
    factory = factory or get_session_factory()
    job = await claim(factory)
    if job is None:
        return False
    try:
        if job.phase == "publish":
            await publish(factory, job)
        else:
            async with asyncio.timeout(140):
                if token_loader:
                    token = await token_loader(job.user_id)
                else:
                    async with factory() as session:
                        token = await get_valid_access_token(session, User(id=job.user_id))
                phase, cursor, changes = await fetch(job, token, transport)
            await checkpoint(factory, job, phase, cursor, changes)
    except Exception as error:
        await failure(factory, job, error)
    return True


async def main():
    from app.operations.health import pulse

    while True:
        pulse("sync")
        try:
            worked = await run_once()
        except Exception:
            logging.getLogger(__name__).warning(
                "Mailbox sync iteration failed; checkpoint retained"
            )
            worked = False
        if not worked:
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
