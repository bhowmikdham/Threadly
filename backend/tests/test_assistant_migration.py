"""Exercise empty install, baseline upgrade, data preservation and downgrade on temporary DBs."""

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

pytestmark = needs_pg
BASELINE = "26902c33da74"
HEAD = "3c6e9a1207bd"
NEW_TABLES = {
    "context_snapshots",
    "assistant_tasks",
    "assistant_jobs",
    "task_events",
    "artifact_revisions",
}


def test_migration_installs_and_preserves_existing_mailbox():
    url = make_url(get_settings().database_url)
    admin_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    database = "threadly_migration_" + uuid4().hex
    test_url = url.set(database=database)
    test_dsn = test_url.set(drivername="postgresql").render_as_string(hide_password=False)

    async def admin(sql):
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    async def execute(sql, *, script=False):
        connection = await asyncpg.connect(test_dsn)
        try:
            if script:
                return await connection.execute(sql)
            return await connection.fetch(sql)
        finally:
            await connection.close()

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": test_url.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    asyncio.run(admin(f'CREATE DATABASE "{database}"'))
    try:
        # Empty database -> current schema, with no ORM create_all assistance.
        migrate("upgrade", "head")
        tables = asyncio.run(execute("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        assert NEW_TABLES <= {row["tablename"] for row in tables}
        migrate("check")
        migrate("downgrade", BASELINE)
        asyncio.run(
            execute(
                """
            INSERT INTO users (id, google_sub, email)
              VALUES (91, 'migration-user', 'test@example.test');
            INSERT INTO threads (id, user_id, gmail_thread_id, subject)
              VALUES (91, 91, 'existing-thread', 'Keep this subject');
            INSERT INTO messages (user_id, thread_id, gmail_msg_id, body_clean, is_from_user)
              VALUES (91, 91, 'existing-message', 'Keep the existing body', false);
        """,
                script=True,
            )
        )
        migrate("upgrade", "head")
        rows = asyncio.run(
            execute(
                "SELECT body_clean, received_at, reply_metadata FROM messages "
                "WHERE gmail_msg_id='existing-message'"
            )
        )
        assert rows[0]["body_clean"] == "Keep the existing body"
        assert rows[0]["received_at"] is None
        assert rows[0]["reply_metadata"] is None
        assert asyncio.run(execute("SELECT version FROM threads WHERE id=91"))[0]["version"] == 0
        assert (
            asyncio.run(execute("SELECT version_num FROM alembic_version"))[0]["version_num"]
            == HEAD
        )
        migrate("downgrade", BASELINE)
        assert (
            asyncio.run(execute("SELECT subject FROM threads WHERE id=91"))[0]["subject"]
            == "Keep this subject"
        )
    finally:
        asyncio.run(admin(f'DROP DATABASE "{database}" WITH (FORCE)'))
