"""Real migrated schema guards, preservation and safe rollback for command plans."""

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
def test_command_plan_migration_guards_and_preserves_old_task():
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_plan_" + uuid4().hex)

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
        migrate("upgrade", "a0426e9bc731")
        execute("""INSERT INTO users(id,google_sub,email) VALUES
          (81,'planner-owner','owner@example.test'),(82,'other','other@example.test');
          INSERT INTO assistant_tasks(id,user_id,request_id,request_hash,instruction,state,version,
            latest_sequence,release)
            VALUES('old',81,'old-request','old-hash','Old task','queued',1,1,'{}');""")
        migrate("upgrade", "head")
        migrate("check")
        assert (
            execute("SELECT request_hash FROM assistant_tasks WHERE id='old'")[0][0] == "old-hash"
        )
        execute("""INSERT INTO command_plans
          (id,user_id,request_id,request_hash,request,release,state,expires_at)
          VALUES('plan',81,'new-request',repeat('a',64),'{}','{}','planning',
            now()+interval '15 minutes');""")
        for sql in [
            "UPDATE command_plans SET request='{}'::jsonb || '{\"changed\":true}'::jsonb",
            "UPDATE command_plans SET state='consumed',task_id='old'",
            "UPDATE command_plans SET expires_at=now()",
        ]:
            with pytest.raises(asyncpg.CheckViolationError):
                execute(sql)
        execute("UPDATE command_plans SET state='proposed',result='{}',plan_hash=repeat('b',64)")
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE command_plans SET result='{\"changed\":true}'")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute("""INSERT INTO command_plans(id,user_id,request_id,request_hash,request,release,
              state,result,plan_hash,task_id,expires_at)
              VALUES('foreign',82,'foreign',repeat('a',64),
              '{}','{}','consumed','{}',repeat('b',64),'old',now()+interval '1 hour')""")
        execute("UPDATE command_plans SET state='consumed',task_id='old'")
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE command_plans SET state='proposed',task_id=NULL")
        migrate("downgrade", "a0426e9bc731", fails=True)
        # Test-only fixture removal; production rollback must retain user data.
        execute("DELETE FROM command_plans")
        migrate("downgrade", "a0426e9bc731")
        assert (
            execute("SELECT request_hash FROM assistant_tasks WHERE id='old'")[0][0] == "old-hash"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
