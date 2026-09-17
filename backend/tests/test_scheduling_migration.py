"""Real prior-head upgrade and full scheduling continuation on Alembic-installed tables."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.assistant import worker
from app.auth import crypto
from app.auth import service as auth_service
from app.auth.google import CALENDAR_SCOPES
from app.calendar import client as calendar_client
from app.calendar import service, slots
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import User
from tests.conftest import needs_pg
from tests.test_slot_service import saved


@needs_pg
def test_scheduling_upgrade_immutability_rollback_and_api(client, auth_headers, monkeypatch):
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_scheduling_" + uuid4().hex)

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

    engine = create_async_engine(target, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(query(f'CREATE DATABASE "{target.database}"', admin=True))
    try:
        migrate("upgrade", "e14026e9a034")
        execute("""INSERT INTO users(id,google_sub,email)
          VALUES(81,'schedule-owner','owner@example.test');
          INSERT INTO assistant_tasks
          (id,user_id,request_id,request_hash,instruction,state,version,latest_sequence,release)
          VALUES('legacy',81,'legacy',repeat('a',64),
                 'Keep this historical task','failed',1,1,'{}')""")
        migrate("upgrade", "head")
        migrate("check")
        assert (
            execute("SELECT scheduling_input FROM assistant_tasks WHERE id='legacy'")[0][0] is None
        )

        async def override():
            async with factory() as session:
                yield session

        client.app.dependency_overrides[get_session] = override
        for module in (service, slots, auth_service):
            monkeypatch.setattr(module, "get_session_factory", lambda: factory)

        async def connect_user():
            async with factory.begin() as session:
                user = await session.get(User, 81)
                user.google_connected = True
                user.google_email_verified = True
                user.google_scopes = CALENDAR_SCOPES
                user.access_token_enc = crypto.encrypt_token("fixture")
                user.access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        asyncio.run(connect_user())
        calls = []

        async def handler(req):
            calls.append(req)
            if req.url.path.endswith("calendarList"):
                return httpx.Response(
                    200, json={"items": [{"id": "work@example.test", "accessRole": "reader"}]}
                )
            body = json.loads(req.content)
            return httpx.Response(
                200,
                json={
                    "timeMin": body["timeMin"],
                    "timeMax": body["timeMax"],
                    "calendars": {"work@example.test": {"busy": []}},
                },
            )

        original = calendar_client._request

        async def transport(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return await original(*args, **kwargs)

        monkeypatch.setattr(calendar_client, "_request", transport)
        h = auth_headers(81)
        assert client.put("/calendar/preferences", headers=h, json=saved()).status_code == 200
        response = client.post(
            "/assistant/scheduling-requests",
            headers=h,
            json={
                "schema_version": "1.0",
                "request_id": "schedule",
                "operation": "check_time",
                "expected_preferences_version": 1,
                "constraints": {"date": "tomorrow", "at_time": "4"},
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        assert asyncio.run(worker.run_once(factory))
        task = client.get(f"/assistant/tasks/{task_id}", headers=h).json()
        assert task["state"] == "needs_clarification"
        response = client.post(
            task["question"]["input_url"],
            headers=h,
            json={
                "schema_version": "1.0",
                "request_id": "answer",
                "expected_version": task["version"],
                "question_id": task["question"]["question_id"],
                "answer": {"meridiem": "PM"},
            },
        )
        assert response.status_code == 202, response.text
        assert asyncio.run(worker.run_once(factory))
        task = client.get(f"/assistant/tasks/{task_id}", headers=h).json()
        assert task["state"] == "succeeded", task
        result = client.get(f"/assistant/artifacts/{task['artifact_id']}", headers=h).json()
        assert (
            result["scheduling_status"]["usable"]
            and len(result["artifact"]["content"]["slots"]) == 1
        )
        assert sum(req.url.path.endswith("freeBusy") for req in calls) == 1
        with pytest.raises(asyncpg.CheckViolationError):
            execute(f"UPDATE assistant_tasks SET scheduling_input='{{}}' WHERE id='{task_id}'")
        with pytest.raises(asyncpg.CheckViolationError):
            execute(f"UPDATE assistant_tasks SET intent_hint=NULL WHERE id='{task_id}'")
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE assistant_tasks SET scheduling_input='{}' WHERE id='legacy'")
        migrate("downgrade", "e14026e9a034", fails=True)
        execute(f"DELETE FROM assistant_tasks WHERE id='{task_id}'")
        migrate("downgrade", "e14026e9a034")
        assert (
            execute("SELECT instruction FROM assistant_tasks WHERE id='legacy'")[0][0]
            == "Keep this historical task"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
