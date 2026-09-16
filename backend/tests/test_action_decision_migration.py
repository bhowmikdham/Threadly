"""Real migration guards plus approval/cutoff services on the migrated schema."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.actions import approval, email_preview
from app.api.errors import ApiError
from app.config import get_settings
from app.db.models import ActionApproval, ActionDecision, ActionJob, AssistantAction, User
from tests.conftest import needs_pg
from tests.test_action_approval import approve_request, candidate, fake_dispatch, stop_request


@needs_pg
def test_decision_migration_preserves_history_and_guards_services():
    url = make_url(get_settings().database_url)
    database = "threadly_decision_" + uuid4().hex
    target = url.set(database=database)

    async def query(sql, admin=False):
        conn = await asyncpg.connect(
            (url if admin else target)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        try:
            if sql.lstrip().upper().startswith("SELECT"):
                return await conn.fetch(sql)
            return await conn.execute(sql)
        finally:
            await conn.close()

    def execute(sql):
        return asyncio.run(query(sql))

    def migrate(*args, fails=False):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": target.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fails:
            assert result.returncode and "Cannot downgrade" in result.stderr
        else:
            assert result.returncode == 0, result.stderr

    async def seed_and_approve():
        engine = create_async_engine(target)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory.begin() as session:
                session.add(User(id=91, google_sub="decision-owner", email="owner@example.test"))
                session.add(User(id=92, google_sub="decision-other", email="other@example.test"))
            action = await candidate(factory, 91)
            # Force expiry exactly at insertion, after service validation, without
            # timing sleeps. Exercise the real PostgreSQL guard and error translation.
            await query("""CREATE FUNCTION test_expiry() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN NEW.expires_at = clock_timestamp() - interval '1 second'; RETURN NEW; END $$;
                CREATE TRIGGER a_test_expiry BEFORE INSERT ON action_approvals
                FOR EACH ROW EXECUTE FUNCTION test_expiry();""")
            try:
                with pytest.raises(ApiError) as failed:
                    async with factory.begin() as session:
                        await approval.approve(
                            session, 91, action.id, approve_request(action), execution_enabled=True
                        )
                assert failed.value.code == "action_approval_blocked"
                assert failed.value.detail == {"blockers": ["action_expired"]}
            finally:
                await query(
                    "DROP TRIGGER a_test_expiry ON action_approvals; DROP FUNCTION test_expiry();"
                )
            async with factory() as session:
                assert (await session.get(AssistantAction, action.id)).state == "proposed"
                assert await session.scalar(select(ActionApproval.id)) is None
                assert await session.scalar(select(ActionJob.action_id)) is None
            async with factory.begin() as session:
                await approval.approve(
                    session, 91, action.id, approve_request(action), execution_enabled=True
                )
            assert await fake_dispatch(factory, 91, action.id)
            return action.id, action.payload_hash, action.payload
        finally:
            await engine.dispose()

    async def late_cancel(action_id):
        engine = create_async_engine(target)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory.begin() as session:
                result = await approval.stop(session, 91, action_id, "cancel", stop_request(3))
                assert result["decision"] == "cancellation_requested"
            async with factory() as session:
                view = await email_preview.view(session, 91, action_id)
                assert view["cancellation_requested"] and view["state"] == "executing"
                assert (await session.get(ActionJob, action_id)).state == "running"
                assert await session.scalar(select(ActionDecision.id))
                assert await session.scalar(select(ActionApproval.id))
                action = await session.get(AssistantAction, action_id)
                return action.payload_hash, action.payload
        finally:
            await engine.dispose()

    asyncio.run(query(f'CREATE DATABASE "{database}"', admin=True))
    try:
        migrate("upgrade", "f1a2b3c4d5e6")
        # Existing draft + approved/dispatched action survive the additive migration.
        # B04's approve uses the new read table, so seed on head then legally downgrade
        # the empty decisions table to establish representative pre-upgrade history.
        migrate("upgrade", "head")
        action_id, hashed, payload = asyncio.run(seed_and_approve())
        migrate("downgrade", "f1a2b3c4d5e6")
        assert execute("SELECT count(*) FROM assistant_actions")[0][0] == 1
        migrate("upgrade", "head")
        migrate("check")
        assert asyncio.run(late_cancel(action_id)) == (hashed, payload)
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE action_decisions SET request_hash=repeat('f',64)")
        with pytest.raises(asyncpg.CheckViolationError):
            execute("DELETE FROM action_decisions")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute(f"""INSERT INTO action_decisions(id,action_id,user_id,operation,request_id,
                       request_hash,expected_version,decision) VALUES('forged','{action_id}',92,
                       'cancel','bad',repeat('a',64),3,'cancellation_requested')""")
        with pytest.raises(asyncpg.CheckViolationError):
            execute(f"""INSERT INTO action_decisions(id,action_id,user_id,operation,request_id,
                       request_hash,expected_version,decision) VALUES('bad-op','{action_id}',91,
                       'approve','bad',repeat('a',64),3,'cancelled')""")
        migrate("downgrade", "f1a2b3c4d5e6", fails=True)
        assert execute("SELECT count(*) FROM action_decisions")[0][0] == 1
    finally:
        asyncio.run(query(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', admin=True))
