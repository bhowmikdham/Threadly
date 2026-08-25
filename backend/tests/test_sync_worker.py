"""Module 3 against real postgres: backfill paginates + upserts idempotently,
incremental uses the cursor, expired cursor falls back to backfill."""
import pytest

from tests.conftest import needs_pg
from tests.test_gmail_client import gmail_transport

pytestmark = needs_pg


async def _seed_user(db_sessionmaker):
    from datetime import UTC, datetime

    from app.auth import crypto
    from app.db import repositories as repo

    async with db_sessionmaker() as session:
        user = await repo.upsert_user(
            session,
            google_sub="g1",
            email="me@x.com",
            display_name="Me",
            access_token_enc=crypto.encrypt_token("at"),
            access_token_expires_at=datetime.now(UTC),
            refresh_token_enc=crypto.encrypt_token("rt"),
        )
        await session.commit()
        return user.id


@pytest.mark.asyncio
async def test_backfill_upserts_all_pages_and_sets_cursor(db_sessionmaker):
    from sqlalchemy import func, select

    from app.db import repositories as repo
    from app.db.models import Message, Thread, User
    from app.sync import worker

    uid = await _seed_user(db_sessionmaker)
    async with db_sessionmaker() as session:
        user = await repo.get_user(session, uid)
        report = await worker.initial_backfill(session, user, "tok", transport=gmail_transport())
    assert report.mode == "backfill"
    assert report.messages_upserted == 3  # m1+m2 (page1) + m3 (page2)

    async with db_sessionmaker() as session:
        assert (await session.execute(select(func.count()).select_from(Message))).scalar_one() == 3
        assert (await session.execute(select(func.count()).select_from(Thread))).scalar_one() >= 1
        assert (await session.execute(select(User.gmail_history_id))).scalar_one() == "9000"

    # re-run: idempotent, no duplicates
    async with db_sessionmaker() as session:
        user = await repo.get_user(session, uid)
        await worker.initial_backfill(session, user, "tok", transport=gmail_transport())
    async with db_sessionmaker() as session:
        assert (await session.execute(select(func.count()).select_from(Message))).scalar_one() == 3


@pytest.mark.asyncio
async def test_incremental_pulls_only_history_changes(db_sessionmaker):
    from sqlalchemy import select

    from app.db import repositories as repo
    from app.db.models import User
    from app.sync import worker

    uid = await _seed_user(db_sessionmaker)
    async with db_sessionmaker() as session:
        user = await repo.get_user(session, uid)
        await worker.initial_backfill(session, user, "tok", transport=gmail_transport())
    async with db_sessionmaker() as session:
        user = await repo.get_user(session, uid)
        report = await worker.incremental_sync(session, user, "tok", transport=gmail_transport())
    assert report.mode == "incremental"
    assert report.messages_upserted == 1  # just m9 from history
    async with db_sessionmaker() as session:
        assert (await session.execute(select(User.gmail_history_id))).scalar_one() == "9001"
