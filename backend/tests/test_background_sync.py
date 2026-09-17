"""Page checkpoint/restart and atomic mailbox publication with fake Gmail."""

from sqlalchemy import func, select

from app.db.models import MailSyncJob, MailSyncStage, Message, User
from app.sync import jobs
from tests.conftest import needs_pg
from tests.test_gmail_client import gmail_transport
from tests.test_sync_worker import _seed_user

pytestmark = needs_pg


async def seed(factory):
    uid = await _seed_user(factory)
    async with factory.begin() as session:
        user = await session.get(User, uid)
        user.google_connected, user.google_email_verified = True, True
        user.google_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        row = await jobs.submit(session, uid, "sync-1")
    return uid, row.id


async def token(owner):
    return "fixture"


async def test_paged_restart_and_atomic_publication(db_sessionmaker):
    uid, job_id = await seed(db_sessionmaker)
    transport = gmail_transport()
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)  # start
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)  # page 1
    async with db_sessionmaker() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.phase == "listing" and row.cursor["page_token"] == "page2"
        assert await session.scalar(select(func.count()).select_from(MailSyncStage)) == 2
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
    # A separate worker invocation resumes page 2, with no process-local state.
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)  # history
    async with db_sessionmaker() as session:
        assert (await session.get(MailSyncJob, job_id)).phase == "publish"
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)
    async with db_sessionmaker.begin() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.state == "succeeded", row.error_code
        assert await session.scalar(select(func.count()).select_from(Message)) == 3
        assert await session.scalar(select(func.count()).select_from(MailSyncStage)) == 0
        assert (await session.get(User, uid)).gmail_history_id == "9000"
        assert (await jobs.submit(session, uid, "sync-1")).id == job_id
    assert not await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)


async def test_newer_inline_sync_or_disconnect_fences_publish(db_sessionmaker):
    uid, job_id = await seed(db_sessionmaker)
    transport = gmail_transport()
    for _ in range(4):
        assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, uid)
        user.sync_version += 1
    assert await jobs.run_once(db_sessionmaker, transport=transport, token_loader=token)
    async with db_sessionmaker() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.state == "failed" and row.error_code == "sync_source_changed"
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert (await session.get(User, uid)).gmail_history_id is None


async def test_lease_recovery_rejects_late_page(db_sessionmaker):
    uid, job_id = await seed(db_sessionmaker)
    first = await jobs.claim(db_sessionmaker)
    async with db_sessionmaker.begin() as session:
        row = await session.get(MailSyncJob, job_id)
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp()))
    second = await jobs.claim(db_sessionmaker)
    assert first.lease_token != second.lease_token
    await jobs.checkpoint(db_sessionmaker, first, "listing", {"page_token": "stale"}, [])
    async with db_sessionmaker() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.phase == "start" and row.lease_token == second.lease_token


async def test_page_failure_never_publishes_partial_mailbox(db_sessionmaker):
    from app.sync.gmail import GmailError

    uid, job_id = await seed(db_sessionmaker)
    claim = await jobs.claim(db_sessionmaker)
    await jobs.failure(db_sessionmaker, claim, GmailError("synthetic private provider detail", 503))
    async with db_sessionmaker.begin() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.state == "queued" and row.error_code == "mailbox_sync_unavailable"
        assert row.lease_token is None
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert (await session.get(User, uid)).gmail_history_id is None
        row.available_at = await session.scalar(select(func.clock_timestamp()))
        row.attempts = 5
    assert await jobs.claim(db_sessionmaker) is None
    async with db_sessionmaker() as session:
        assert (await session.get(MailSyncJob, job_id)).state == "failed"


async def test_expired_history_restarts_full_scan(db_sessionmaker):
    from app.sync.gmail import GmailError

    _, job_id = await seed(db_sessionmaker)
    async with db_sessionmaker.begin() as session:
        row = await session.get(MailSyncJob, job_id)
        row.phase, row.full = "history", False
        row.cursor = {"start_history": "123"}
        session.add(
            MailSyncStage(job_id=job_id, user_id=row.user_id, message_id="stale", payload=None)
        )
    claim = await jobs.claim(db_sessionmaker)
    await jobs.failure(db_sessionmaker, claim, GmailError("expired", 404))
    async with db_sessionmaker() as session:
        row = await session.get(MailSyncJob, job_id)
        assert row.phase == "start" and row.full and row.resets == 1
        assert await session.scalar(select(func.count()).select_from(MailSyncStage)) == 0
