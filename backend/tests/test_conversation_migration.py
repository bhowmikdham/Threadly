"""Conversation migration from previous schema, drift check and lossless rollback guard."""

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
def test_conversation_migration_preservation_and_safe_rollback():
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_chat_" + uuid4().hex)

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
        migrate("upgrade", "b17026e9a038")
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
        execute(
            """INSERT INTO conversations(
              id,user_id,version,account_version,state_enc,expires_at
            ) VALUES('chat',81,0,1,'\\x0001',now()+interval '7 days')"""
        )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE conversations SET version=-1 WHERE id='chat'")
        migrate("downgrade", "b17026e9a038", fails=True)
        execute("DELETE FROM conversations")
        migrate("downgrade", "b17026e9a038")
        assert (
            execute("SELECT request_hash FROM assistant_tasks WHERE id='old'")[0][0] == "old-hash"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
