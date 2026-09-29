"""Session generation upgrades existing accounts and guards unsafe rollback."""

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
def test_session_generation_migration_preserves_accounts_and_blocks_connected_downgrade():
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_sessions_" + uuid4().hex)

    async def query(sql, *, admin=False):
        dsn = (
            (url if admin else target)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        conn = await asyncpg.connect(dsn)
        try:
            if sql.lstrip().startswith("SELECT"):
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
            env={
                **os.environ,
                "DATABASE_URL": target.render_as_string(hide_password=False),
                "THREADLY_AUTH_SERVICES_STOPPED": "0" if fails else "1",
                "THREADLY_SESSION_SIGNING_KEY_ROTATED": "0" if fails else "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fails:
            assert result.returncode and "offline key rotation" in result.stderr
        else:
            assert result.returncode == 0, result.stderr

    asyncio.run(query(f'CREATE DATABASE "{target.database}"', admin=True))
    try:
        migrate("upgrade", "c23026e9a039")
        execute("""
          INSERT INTO users(id,google_sub,email,google_connected,google_account_version,
            access_token_enc)
          VALUES(1,'connected','one@example.test',TRUE,3,decode('aabb','hex')),
                (2,'disconnected','two@example.test',FALSE,4,NULL)
        """)
        execute("""
          INSERT INTO google_oauth_sessions(state_hash,code_challenge,redirect_uri,
            user_id,account_version,expires_at)
          VALUES(repeat('a',64),repeat('b',43),'https://ext.chromiumapp.org/',
            1,3,now()+interval '10 minutes')
        """)
        migrate("upgrade", "head")
        migrate("check")
        rows = execute(
            "SELECT id,google_account_version,threadly_session_version,access_token_enc "
            "FROM users ORDER BY id"
        )
        assert [
            (row["id"], row["google_account_version"], row["threadly_session_version"])
            for row in rows
        ] == [(1, 3, 1), (2, 4, 1)]
        assert rows[0]["access_token_enc"] == bytes.fromhex("aabb")
        assert execute("SELECT count(*) FROM google_oauth_sessions")[0][0] == 0
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE users SET threadly_session_version=0 WHERE id=1")
        execute("""
          INSERT INTO google_oauth_sessions(state_hash,code_challenge,redirect_uri,
            user_id,account_version,session_version,expires_at)
          VALUES(repeat('b',64),repeat('b',43),'https://ext.chromiumapp.org/',
            1,3,1,now()+interval '10 minutes')
        """)
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE google_oauth_sessions SET session_version=NULL WHERE user_id=1")
        execute("UPDATE users SET threadly_session_version=2 WHERE id=1")
        migrate("downgrade", "c23026e9a039", fails=True)
        migrate("downgrade", "c23026e9a039")
        assert execute("SELECT access_token_enc FROM users WHERE id=1")[0][0] == bytes.fromhex(
            "aabb"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)', admin=True))
