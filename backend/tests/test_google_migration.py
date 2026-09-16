"""Exercise state/immutability triggers on migrated PostgreSQL, not ORM create_all."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url

from app.config import get_settings
from tests.conftest import needs_pg


@needs_pg
def test_google_migration_preserves_legacy_tokens_and_unknown_grants():
    url = make_url(get_settings().database_url)
    database = "threadly_google_" + uuid4().hex
    target = url.set(database=database)

    async def query(sql, admin=False):
        dsn = (
            (url if admin else target)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        conn = await asyncpg.connect(dsn)
        try:
            if sql.lstrip().upper().startswith("SELECT"):
                return await conn.fetch(sql)
            return await conn.execute(sql)
        finally:
            await conn.close()

    def execute(sql):
        return asyncio.run(query(sql))

    def reject(sql, error=asyncpg.CheckViolationError):
        import pytest

        with pytest.raises(error):
            execute(sql)

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

    asyncio.run(query(f'CREATE DATABASE "{database}"', admin=True))
    try:
        migrate("upgrade", "d9302f5b7a14")
        execute("""INSERT INTO users(id,google_sub,email,access_token_enc,refresh_token_enc)
          VALUES(1,'legacy','legacy@example.test',decode('aabb','hex'),decode('ccdd','hex'));
          INSERT INTO users(id,google_sub,email) VALUES(2,'no-token','empty@example.test');""")
        migrate("upgrade", "head")
        migrate("check")
        rows = execute("""SELECT google_connected,google_scopes,google_email_verified,
                          google_account_version,google_token_version,access_token_enc,
                          refresh_token_enc FROM users ORDER BY id""")
        assert tuple(rows[0]) == (
            True,
            None,
            None,
            1,
            1,
            bytes.fromhex("aabb"),
            bytes.fromhex("ccdd"),
        )
        assert rows[1][0] is False and rows[1][1] is None
        reject("UPDATE users SET google_token_version=0 WHERE id=1")
        execute("""INSERT INTO google_oauth_sessions(state_hash,code_challenge,redirect_uri,
                 user_id,account_version,expires_at) VALUES(repeat('a',64),repeat('b',43),
                 'https://test.chromiumapp.org/',1,1,now()+interval '10 minutes')""")
        reject("UPDATE google_oauth_sessions SET account_version=NULL")
        migrate("downgrade", "d9302f5b7a14")
        assert execute("SELECT refresh_token_enc FROM users WHERE id=1")[0][0] == bytes.fromhex(
            "ccdd"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', admin=True))
