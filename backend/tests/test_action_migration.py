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
def test_migrated_action_guards_and_history_preservation():
    url = make_url(get_settings().database_url)
    database = "threadly_actions_" + uuid4().hex
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
        migrate("upgrade", "c8291e4a6f03")
        execute("""INSERT INTO users(id,google_sub,email)
            VALUES(91,'action-test','owner@example.test');
            INSERT INTO assistant_tasks(id,user_id,request_id,request_hash,instruction,
              state,version,latest_sequence,release)
              VALUES('task',91,'request','hash','Draft','succeeded',1,1,'{}');
            INSERT INTO artifact_revisions(id,task_id,user_id,revision,payload,provenance,
              draft_envelope)
              VALUES('draft', 'task',91,1,
                '{"kind":"draft"}','{}','{"to":["recipient@example.test"]}');
            INSERT INTO artifact_revisions(id,task_id,user_id,revision,payload,provenance,
              draft_envelope,edit_request_id,edit_request_hash)
              VALUES('edited','task',91,2,
                '{"kind":"draft"}','{}','{"to":["updated@example.test"]}','edit','hash');
            UPDATE assistant_tasks SET final_artifact_id='edited' WHERE id='task';
            INSERT INTO draft_reviews(artifact_id,user_id,payload_hash)
              VALUES('edited',91,repeat('d',64));
        """)
        migrate("upgrade", "head")
        migrate("check")
        assert execute("SELECT final_artifact_id FROM assistant_tasks")[0][0] == "edited"
        assert execute("SELECT payload_hash FROM draft_reviews")[0][0] == "d" * 64
        assert [
            r[0] for r in execute("SELECT revision FROM artifact_revisions ORDER BY revision")
        ] == [1, 2]
        execute("""INSERT INTO assistant_actions(id,user_id,task_id,artifact_id,action_type,
          proposal_request_id,proposal_hash,payload_schema,payload,payload_hash,source_artifact_hash,
          source_versions,expires_at)
          VALUES('action',91,'task','edited','send_email','p',repeat('a',64),
          'fixture-1','{"body":"Synthetic preserved payload"}',repeat('b',64),repeat('c',64),
          '{}',now()+interval '1 hour');""")
        reject("UPDATE assistant_actions SET payload='{}' WHERE id='action'")
        reject(
            "UPDATE assistant_actions SET source_versions='{\"changed\":true}' WHERE id='action'"
        )
        reject("UPDATE assistant_actions SET state='succeeded',version=2 WHERE id='action'")
        reject("UPDATE assistant_actions SET state='approved',version=2 WHERE id='action'")
        reject("DELETE FROM assistant_tasks WHERE id='task'", asyncpg.ForeignKeyViolationError)
        reject("DELETE FROM users WHERE id=91", asyncpg.ForeignKeyViolationError)
        execute("""INSERT INTO action_approvals(id,action_id,user_id,action_version,payload_hash,
          request_id,request_hash,expires_at)
          VALUES('approval','action',91,1,repeat('b',64),'approve',
          repeat('a',64),now()+interval '30 minutes');""")
        reject("UPDATE action_approvals SET request_hash=repeat('z',64)")
        execute("UPDATE assistant_actions SET state='approved',version=2 WHERE id='action'")
        reject("UPDATE assistant_actions SET state='executing',version=3 WHERE id='action'")
        execute("""INSERT INTO action_jobs(action_id,user_id,approval_id)
          VALUES('action',91,'approval');
          INSERT INTO action_attempts(id,action_id,user_id,approval_id,number,action_version,
          lease_token,state,dispatch_intent_at,provider_identifiers)
          VALUES('attempt','action',91,'approval',1,2,
          'lease','dispatched',now(),'{"message_id":"synthetic@example.test"}');""")
        reject(
            """INSERT INTO action_attempts(id,action_id,user_id,approval_id,number,action_version,
          lease_token,state,dispatch_intent_at,provider_identifiers)
          VALUES('duplicate','action',91,'approval',2,2,
          'lease2','dispatched',now(),'{}')""",
            asyncpg.UniqueViolationError,
        )
        execute("UPDATE assistant_actions SET state='executing',version=3 WHERE id='action'")
        reject("UPDATE assistant_actions SET state='cancelled',version=4 WHERE id='action'")
        reject("UPDATE action_attempts SET provider_identifiers='{}' WHERE id='attempt'")
        execute("UPDATE action_attempts SET state='outcome_unknown' WHERE id='attempt'")
        execute("UPDATE assistant_actions SET state='outcome_unknown',version=4 WHERE id='action'")
        reject("UPDATE action_attempts SET state='dispatched' WHERE id='attempt'")
        reject("DELETE FROM action_attempts WHERE id='attempt'")
        reject("DELETE FROM assistant_actions WHERE id='action'")
        reject("UPDATE assistant_actions SET state='failed',version=5 WHERE id='action'")
        reject("UPDATE action_attempts SET state='succeeded' WHERE id='attempt'")
        migrate("downgrade", "c8291e4a6f03", fails=True)
        assert execute("SELECT state FROM assistant_actions")[0][0] == "outcome_unknown"
        assert execute("SELECT state FROM action_jobs")[0][0] == "held"
        execute(
            "UPDATE action_attempts SET state='succeeded',evidence='{\"synthetic_test\":true}' "
            "WHERE id='attempt'"
        )
        execute(
            "UPDATE assistant_actions SET state='succeeded',version=5,result='{\"fixture\":true}' "
            "WHERE id='action'"
        )
        reject("UPDATE assistant_actions SET state='approved',version=6 WHERE id='action'")
        reject("UPDATE action_attempts SET evidence='{}' WHERE id='attempt'")
        assert (
            "Synthetic preserved payload"
            in execute("SELECT payload::text FROM assistant_actions")[0][0]
        )
        # Explicit test-only deletion of resolved history; never an automatic product path.
        execute(
            "DELETE FROM action_jobs; DELETE FROM action_attempts; "
            "DELETE FROM action_approvals; DELETE FROM assistant_actions;"
        )
        migrate("downgrade", "c8291e4a6f03")
        assert execute("SELECT payload_hash FROM draft_reviews")[0][0] == "d" * 64
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(query(f'DROP DATABASE "{database}" WITH (FORCE)', admin=True))
