"""Real B12 -> B13 upgrade, owner fences, immutability and guarded rollback."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.config import get_settings
from tests.conftest import needs_pg


@needs_pg
def test_slot_migration_guards_and_preserves_calendar_data():
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_slots_" + uuid4().hex)

    async def query(sql, admin=False):
        dsn = (
            (url if admin else target)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        conn = await asyncpg.connect(dsn)
        try:
            return (
                await conn.fetch(sql)
                if sql.lstrip().startswith("SELECT")
                else await conn.execute(sql)
            )
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

    def insert(identifier="query", owner=81, parent="NULL", evidence="NULL", extra=""):
        execute(f"""INSERT INTO calendar_slot_requests
          (id,user_id,request_id,request_hash,request,anchor_from_request_id,anchor_at,
           preferences_version,account_version,policy_version,preferences,state,resolution,
           evidence_id,created_at,expires_at)
          VALUES('{identifier}',{owner},'{identifier}',repeat('a',64),'{{}}',{parent},now(),
                 1,1,'calendar-slots-1.0.0','{{}}','processing','{{}}',{evidence},
                 now(),now()+interval '5 minutes'){extra}""")

    asyncio.run(query(f'CREATE DATABASE "{target.database}"', admin=True))
    try:
        migrate("upgrade", "c12026e9a032")
        execute("""INSERT INTO users(id,google_sub,email) VALUES
          (81,'slots-owner','owner@example.test'),(82,'slots-other','other@example.test');
          INSERT INTO calendar_preferences
            (user_id,version,account_version,policy_version,preferences)
          VALUES(81,1,1,'calendar-read-1.0.0','{"timezone":"UTC"}'),
                (82,1,1,'calendar-read-1.0.0','{"timezone":"UTC"}');
          INSERT INTO calendar_evidence
            (id,user_id,preferences_version,account_version,policy_version,
            checked_at,expires_at,result)
          VALUES('evidence',81,1,1,'calendar-read-1.0.0',now(),now()+interval '5 minutes','{}');""")
        migrate("upgrade", "head")
        migrate("check")
        assert (
            execute("SELECT preferences FROM calendar_preferences WHERE user_id=81")[0][0]
            == '{"timezone": "UTC"}'
        )
        assert execute("SELECT id FROM calendar_evidence")[0][0] == "evidence"
        insert()
        with pytest.raises(asyncpg.UniqueViolationError):
            insert()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            insert("wrong-evidence", owner=82, evidence="'evidence'")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            insert("wrong-anchor", owner=82, parent="'query'")
        insert("child", parent="'query'")
        for assignment in (
            "anchor_at=now()+interval '1 day'",
            "request='{}'",
            "request_hash=repeat('b',64)",
            "preferences_version=2",
            "user_id=82",
            "resolution='{\"changed\":true}'",
            "state='complete', expires_at=expires_at+interval '1 hour'",
            "state='complete', request='{\"changed\":true}'",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                execute(f"UPDATE calendar_slot_requests SET {assignment} WHERE id='query'")
        execute("""UPDATE calendar_slot_requests SET state='complete', evidence_id='evidence',
          calculated_at=now(),result='{"slots":[]}',expires_at=expires_at-interval '1 second'
          WHERE id='query'""")
        for assignment in (
            "result='{}'",
            "state='processing'",
            "evidence_id=NULL",
            "expires_at=expires_at-interval '1 second'",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                execute(f"UPDATE calendar_slot_requests SET {assignment} WHERE id='query'")
        execute(
            "UPDATE calendar_slot_requests SET state='failed',error_code='fixture' WHERE id='child'"
        )
        migrate("downgrade", "c12026e9a032", fails=True)
        assert execute("SELECT count(*) FROM calendar_slot_requests")[0][0] == 2
        # Deliberate fixture cleanup only; production downgrade never deletes these records.
        execute("DELETE FROM calendar_slot_requests WHERE id='query'")
        assert execute("SELECT count(*) FROM calendar_slot_requests")[0][0] == 0
        migrate("downgrade", "c12026e9a032")
        assert execute("SELECT id FROM calendar_evidence")[0][0] == "evidence"
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
