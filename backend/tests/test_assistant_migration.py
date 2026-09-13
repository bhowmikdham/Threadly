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
HEAD = "c6e0419a72df"
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

    def migrate(*args, fails=False):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": test_url.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fails:
            assert result.returncode != 0 and "Cannot downgrade" in result.stderr
        else:
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
            INSERT INTO drafts (id, user_id, body, status)
              VALUES (91, 91, 'Keep the legacy draft', 'draft');
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
        # The immediately previous release can have queued summary tasks at upgrade.
        migrate("downgrade", "3c6e9a1207bd")
        asyncio.run(
            execute(
                """
            INSERT INTO context_snapshots (id, user_id, thread_id, source_hash, payload)
              VALUES ('old-context', 91, 91, 'old-hash', '{}');
            INSERT INTO assistant_tasks (id, user_id, request_id, request_hash, instruction,
                context_snapshot_id, state, version, latest_sequence, release)
              VALUES ('old-task', 91, 'old-request', 'old-hash', 'Summarise this',
                'old-context', 'queued', 1, 1, '{"workflow":"summary-task-1.0.0"}');
        """,
                script=True,
            )
        )
        migrate("upgrade", "head")
        old = asyncio.run(execute("SELECT * FROM assistant_tasks WHERE id='old-task'"))[0]
        assert old["context_snapshot_id"] == "old-context" and old["state"] == "queued"
        assert old["route"] is None and old["intent_hint"] is None
        assert old["draft_input"] is None
        assert (
            asyncio.run(execute("SELECT body FROM drafts WHERE id=91"))[0]["body"]
            == "Keep the legacy draft"
        )
        asyncio.run(
            execute(
                """
            INSERT INTO assistant_tasks (id, user_id, request_id, request_hash, instruction,
                state, version, latest_sequence, release, draft_input)
              VALUES ('draft-task', 91, 'draft-request', 'draft-hash', 'Write an email',
                'queued', 1, 1, '{"workflow":"contextual-task-1.1.0"}',
                '{"to":["person@example.test"]}');
        """,
                script=True,
            )
        )
        migrate("downgrade", "b7a219c40e6d", fails=True)
        assert asyncio.run(execute("SELECT draft_input FROM assistant_tasks WHERE id='draft-task'"))
        asyncio.run(execute("DELETE FROM assistant_tasks WHERE id='draft-task'", script=True))
        asyncio.run(
            execute(
                """
            INSERT INTO assistant_tasks (id, user_id, request_id, request_hash, instruction,
                state, version, latest_sequence, release)
              VALUES ('new-task', 91, 'new-request', 'new-hash', 'Summarise this',
                'queued', 1, 1, '{"workflow":"contextual-task-1.0.0"}');
        """,
                script=True,
            )
        )
        migrate("downgrade", "3c6e9a1207bd", fails=True)
        assert asyncio.run(execute("SELECT id FROM assistant_tasks WHERE id='new-task'"))
        assert (
            asyncio.run(execute("SELECT version_num FROM alembic_version"))[0]["version_num"]
            == HEAD
        )
        # Test-only removal, never an automatic downgrade action.
        asyncio.run(execute("DELETE FROM assistant_tasks WHERE id='new-task'", script=True))
        migrate("downgrade", BASELINE)
        assert (
            asyncio.run(execute("SELECT subject FROM threads WHERE id=91"))[0]["subject"]
            == "Keep this subject"
        )
    finally:
        asyncio.run(admin(f'DROP DATABASE "{database}" WITH (FORCE)'))
