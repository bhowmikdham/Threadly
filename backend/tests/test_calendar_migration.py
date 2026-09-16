"""Real Calendar migration constraints, existing-task preservation and guarded rollback."""

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
def test_calendar_migration_guards_and_preserves_old_task():
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_calendar_" + uuid4().hex)

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

    asyncio.run(query(f'CREATE DATABASE "{target.database}"', admin=True))
    try:
        migrate("upgrade", "b10c026e9a31")
        execute("""INSERT INTO users(id,google_sub,email) VALUES
          (81,'calendar-owner','owner@example.test'),(82,'other','other@example.test');
          INSERT INTO assistant_tasks(id,user_id,request_id,request_hash,instruction,state,version,
            latest_sequence,release)
            VALUES('old',81,'old-request','old-hash','Old task','queued',1,1,'{}');""")
        migrate("upgrade", "head")
        migrate("check")
        assert (
            execute("SELECT request_hash FROM assistant_tasks WHERE id='old'")[0][0] == "old-hash"
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute("""INSERT INTO calendar_preferences
              (user_id,version,account_version,policy_version,preferences)
              VALUES(99,1,1,'calendar-read-1.0.0','{}')""")
        execute("""INSERT INTO calendar_preferences
          (user_id,version,account_version,policy_version,preferences)
          VALUES(81,1,1,'calendar-read-1.0.0','{}')""")
        with pytest.raises(asyncpg.CheckViolationError):
            execute('UPDATE calendar_preferences SET preferences=\'{"timezone":"UTC"}\'')
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE calendar_preferences SET user_id=82,version=2")
        execute('UPDATE calendar_preferences SET version=2,preferences=\'{"timezone":"UTC"}\'')
        execute("""INSERT INTO calendar_evidence
          (id,user_id,preferences_version,account_version,policy_version,checked_at,expires_at,result)
          VALUES('evidence',81,2,1,'calendar-read-1.0.0',now(),now()+interval '5 minutes','{}')""")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute("""INSERT INTO calendar_evidence
              (id,user_id,preferences_version,account_version,policy_version,
               checked_at,expires_at,result)
              VALUES('foreign',82,1,1,'policy',now(),now()+interval '5 minutes','{}')""")
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE calendar_evidence SET result='{\"changed\":true}'")
        migrate("downgrade", "b10c026e9a31", fails=True)
        # Test-only cleanup; production downgrade deliberately retains user preferences/evidence.
        execute("DELETE FROM calendar_preferences")
        assert execute("SELECT count(*) FROM calendar_evidence")[0][0] == 0
        migrate("downgrade", "b10c026e9a31")
        assert (
            execute("SELECT request_hash FROM assistant_tasks WHERE id='old'")[0][0] == "old-hash"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
