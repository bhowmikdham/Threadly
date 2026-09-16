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
HEAD = "f1a2b3c4d5e6"
NEW_TABLES = {
    "google_oauth_sessions",
    "context_snapshots",
    "assistant_tasks",
    "assistant_jobs",
    "task_events",
    "artifact_revisions",
    "draft_reviews",
    "task_questions",
    "task_inputs",
    "assistant_steps",
    "assistant_actions",
    "action_approvals",
    "action_attempts",
    "action_jobs",
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
        assert old["continuation_release"] is None and old["input_version"] == 0
        # New continuation state must be preserved, never silently dropped on rollback.
        asyncio.run(
            execute(
                """
            UPDATE assistant_tasks SET continuation_release = '{"version":"test"}', input_version=1
              WHERE id='old-task';
            INSERT INTO task_questions (id, task_id, user_id, task_version, input_version,
                state, payload, expires_at)
              VALUES ('question-old', 'old-task', 91, 1, 0, 'answered', '{}', now());
            INSERT INTO task_inputs (id, task_id, user_id, question_id, request_id,
                request_hash, input_version, answer, effective_fields,
                context_snapshot_id, source_hash)
              VALUES ('input-old', 'old-task', 91, 'question-old', 'answer-old', repeat('a',64),
                1, '{"timezone":"UTC"}', '{"timezone":"UTC"}', 'old-context', 'old-hash');
        """,
                script=True,
            )
        )
        assert (
            asyncio.run(execute("SELECT read_input FROM assistant_tasks WHERE id='old-task'"))[0][
                "read_input"
            ]
            is None
        )
        asyncio.run(
            execute(
                'UPDATE assistant_tasks SET read_input=\'{"operation":"help"}\' '
                "WHERE id='old-task'",
                script=True,
            )
        )
        migrate("downgrade", "f2b6049c7a81", fails=True)
        assert (
            asyncio.run(execute("SELECT read_input FROM assistant_tasks WHERE id='old-task'"))[0][
                "read_input"
            ]
            is not None
        )
        asyncio.run(
            execute("UPDATE assistant_tasks SET read_input=NULL WHERE id='old-task'", script=True)
        )
        migrate("downgrade", "e9b7120c4a63", fails=True)
        assert "UTC" in asyncio.run(execute("SELECT answer FROM task_inputs"))[0]["answer"]
        assert (
            asyncio.run(execute("SELECT version_num FROM alembic_version"))[0]["version_num"]
            == HEAD
        )
        asyncio.run(
            execute(
                """
            DELETE FROM task_inputs;
            DELETE FROM task_questions;
            UPDATE assistant_tasks SET continuation_release=NULL, input_version=0
              WHERE id='old-task';
        """,
                script=True,
            )
        )
        assert (
            asyncio.run(execute("SELECT body FROM drafts WHERE id=91"))[0]["body"]
            == "Keep the legacy draft"
        )
        migrate("downgrade", "c6e0419a72df")
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
        asyncio.run(
            execute(
                """
            INSERT INTO artifact_revisions (id, task_id, user_id, revision, payload, provenance)
              VALUES ('old-draft', 'draft-task', 91, 1,
                '{"kind":"draft","content":{"body":"Keep generated text"}}', '{"model":"old"}');
        """,
                script=True,
            )
        )
        migrate("upgrade", "head")
        artifact = asyncio.run(execute("SELECT * FROM artifact_revisions WHERE id='old-draft'"))[0]
        assert "Keep generated text" in artifact["payload"]
        assert "person@example.test" in artifact["draft_envelope"]
        assert artifact["edit_request_id"] is None
        assert artifact["stream_key"] == "result"
        assert (
            asyncio.run(
                execute("SELECT final_artifact_id FROM assistant_tasks WHERE id='draft-task'")
            )[0]["final_artifact_id"]
            == "old-draft"
        )
        # Multi-stream rollback is deliberately refused, preserving both UUIDs.
        asyncio.run(
            execute(
                """
            UPDATE assistant_tasks SET compound_input='{"template":"summary_then_reply"}'
              WHERE id='draft-task';
            INSERT INTO artifact_revisions
              (id, task_id, user_id, stream_key, revision, payload, provenance)
              VALUES ('summary-step', 'draft-task', 91, 'summary', 1,
                '{"kind":"summary"}', '{}');
            INSERT INTO assistant_steps
              (task_id, ordinal, user_id, operation, state, input_hash, release,
               artifact_id, output_hash)
              VALUES ('draft-task', 1, 91, 'summarise_thread', 'succeeded', repeat('a',64),
                '{}', 'summary-step', repeat('b',64));
        """,
                script=True,
            )
        )
        migrate("downgrade", "b7180d3f9e62", fails=True)
        assert (
            len(
                asyncio.run(execute("SELECT id FROM artifact_revisions WHERE task_id='draft-task'"))
            )
            == 2
        )
        asyncio.run(
            execute(
                """
            DELETE FROM assistant_steps;
            DELETE FROM artifact_revisions WHERE id='summary-step';
            UPDATE assistant_tasks SET compound_input=NULL WHERE id='draft-task';
        """,
                script=True,
            )
        )
        # Review-only data must also prevent destructive downgrade.
        asyncio.run(
            execute(
                """
            INSERT INTO draft_reviews (artifact_id, user_id, payload_hash)
              VALUES ('old-draft', 91, repeat('a', 64));
        """,
                script=True,
            )
        )
        migrate("downgrade", "c6e0419a72df", fails=True)
        assert asyncio.run(execute("SELECT * FROM draft_reviews"))
        asyncio.run(execute("DELETE FROM draft_reviews", script=True))
        asyncio.run(
            execute(
                """
            INSERT INTO artifact_revisions (id, task_id, user_id, revision, payload, provenance,
                draft_envelope, edit_request_id, edit_request_hash)
              VALUES ('edited-draft', 'draft-task', 91, 2, '{"kind":"draft"}', '{}',
                '{"to":["edited@example.test"]}', 'edit-1', repeat('b',64));
        """,
                script=True,
            )
        )
        migrate("downgrade", "c6e0419a72df", fails=True)
        assert asyncio.run(execute("SELECT * FROM artifact_revisions WHERE revision=2"))
        asyncio.run(execute("DELETE FROM artifact_revisions WHERE revision=2", script=True))
        migrate("downgrade", "c6e0419a72df")
        assert (
            "Keep generated text"
            in asyncio.run(execute("SELECT payload FROM artifact_revisions WHERE id='old-draft'"))[
                0
            ]["payload"]
        )
        migrate("upgrade", "head")
        migrate("check")
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
