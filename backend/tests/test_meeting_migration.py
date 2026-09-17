"""Real B13 upgrade, constraints/rollback, and route publication on migrated tables."""

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

from app.auth import crypto
from app.auth import service as auth_service
from app.auth.google import CALENDAR_SCOPES
from app.calendar import client as calendar_client
from app.calendar import negotiations, service, slots
from app.config import get_settings
from app.db.models import User
from tests.conftest import needs_pg
from tests.test_slot_service import saved


@needs_pg
def test_meeting_upgrade_guards_and_migrated_api(client, auth_headers, monkeypatch):
    url = make_url(get_settings().database_url)
    target = url.set(database="threadly_meetings_" + uuid4().hex)

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
        migrate("upgrade", "d13026e9a033")
        execute("""INSERT INTO users(id,google_sub,email) VALUES
          (81,'meeting-owner','owner@example.test'),(82,'meeting-other','other@example.test');
          INSERT INTO threads(id,user_id,gmail_thread_id,version,last_msg_id) VALUES
          (81,81,'thread-81',1,'msg-81'),(82,82,'thread-82',1,'msg-82');
          INSERT INTO calendar_preferences
          (user_id,version,account_version,policy_version,preferences)
          VALUES(81,1,1,'calendar-read-1.0.0','{}'),(82,1,1,'calendar-read-1.0.0','{}');
          INSERT INTO calendar_slot_requests
          (id,user_id,request_id,request_hash,request,anchor_at,preferences_version,account_version,
           policy_version,preferences,state,resolution,created_at,expires_at,result)
          VALUES('old-query',81,'old-key',repeat('a',64),'{}',now(),1,1,
                 'calendar-slots-1.0.0','{}','complete','{}',now(),now()+interval '5 minutes',
                 '{"slots":[]}');""")
        migrate("upgrade", "head")
        migrate("check")
        assert (
            execute("SELECT request_hash FROM calendar_slot_requests WHERE id='old-query'")[0][0]
            == "a" * 64
        )

        def neg(ident, owner=81, thread=81):
            execute(f"""INSERT INTO meeting_negotiations
              (id,user_id,thread_id,request_id,request_hash,policy_version,version,state)
              VALUES('{ident}',{owner},{thread},'{ident}',repeat('a',64),
                     'meeting-negotiation-1.0.0',1,'open')""")

        neg("neg81")
        neg("neg81-b")
        neg("neg82", 82, 82)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            neg("wrong-thread", 81, 82)
        execute("""INSERT INTO meeting_offers
          (id,negotiation_id,user_id,request_id,request_hash,revision,created_version,thread_version,
           slot_request_id,created_at,expires_at)
          VALUES('offer81','neg81',81,'offer',repeat('b',64),1,2,1,'old-query',
                 now(),now()+interval '4 minutes')""")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute("""INSERT INTO meeting_offers
              (id,negotiation_id,user_id,request_id,request_hash,revision,created_version,
               thread_version,slot_request_id,created_at,expires_at)
              VALUES('bad','neg82',82,'bad',repeat('b',64),1,2,1,'old-query',
                     now(),now()+interval '4 minutes')""")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            execute(
                "UPDATE meeting_negotiations SET version=2,state='offered', "
                "current_offer_id='offer81' WHERE id='neg81-b'"
            )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE meeting_negotiations SET version=3 WHERE id='neg81'")
        execute(
            "UPDATE meeting_negotiations SET version=2,state='offered', "
            "current_offer_id='offer81' WHERE id='neg81'"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE meeting_offers SET thread_version=2 WHERE id='offer81'")

        def selection(ident, neg_id="neg81", owner=81):
            execute(f"""INSERT INTO meeting_selections
              (id,negotiation_id,user_id,offer_id,request_id,request_hash,created_version,slot_id,
               state,created_at,expires_at)
              VALUES('{ident}','{neg_id}',{owner},'offer81','{ident}',repeat('c',64),3,'slot',
                     'checking',now(),now()+interval '3 minutes')""")

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            selection("bad-selection", "neg81-b")
        selection("select81")
        execute(
            "UPDATE meeting_negotiations SET version=3,state='checking', "
            "current_selection_id='select81' WHERE id='neg81'"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE meeting_selections SET state='selected',slot_id='invented'")
        execute(
            "UPDATE meeting_selections SET state='selected',checked_slot_request_id='old-query'"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE meeting_selections SET state='checking'")
        execute("UPDATE meeting_negotiations SET version=4,state='selected' WHERE id='neg81'")
        execute(
            "UPDATE meeting_negotiations SET version=5,state='closed', "
            "close_request_id='close',close_request_hash=repeat('d',64) WHERE id='neg81'"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            execute("UPDATE meeting_negotiations SET version=6 WHERE id='neg81'")
        # Now cross the real API -> service -> migrated PostgreSQL -> fake HTTP -> publication.
        for module in (negotiations, slots, service, auth_service):
            monkeypatch.setattr(module, "get_session_factory", lambda: factory)

        async def connect_user():
            async with factory() as session:
                user = await session.get(User, 81)
                user.google_connected = True
                user.google_email_verified = True
                user.google_scopes = CALENDAR_SCOPES
                user.access_token_enc = crypto.encrypt_token("fixture")
                user.access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
                await session.commit()

        asyncio.run(connect_user())

        async def handler(req):
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
        response = client.put("/calendar/preferences", headers=h, json=saved(1))
        assert response.status_code == 200, response.text
        response = client.post(
            "/calendar/slot-requests",
            headers=h,
            json={
                "request_id": "runtime-slots",
                "expected_preferences_version": 2,
                "date": "tomorrow",
            },
        )
        assert response.status_code == 202, response.text
        runtime_query = response.json()
        response = client.post(
            "/calendar/negotiations",
            headers=h,
            json={
                "request_id": "runtime-neg",
                "thread_id": "thread-81",
                "expected_thread_version": 1,
            },
        )
        assert response.status_code == 201, response.text
        path = "/calendar/negotiations/" + response.json()["id"]
        response = client.post(
            path + "/offers",
            headers=h,
            json={
                "request_id": "runtime-offer",
                "expected_version": 1,
                "expected_thread_version": 1,
                "slot_request_id": runtime_query["id"],
            },
        )
        assert response.status_code == 201, response.text
        offered = response.json()
        response = client.post(
            path + "/selections",
            headers=h,
            json={
                "request_id": "runtime-select",
                "expected_version": 2,
                "offer_id": offered["id"],
                "slot_id": offered["slots"][0]["id"],
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "selected" and response.json()["usable"]
        migrate("downgrade", "d13026e9a033", fails=True)
        assert execute("SELECT count(*) FROM meeting_negotiations")[0][0] == 4
        # Test-only cleanup; downgrade itself must never delete user records.
        execute("DELETE FROM meeting_negotiations")
        assert execute("SELECT count(*) FROM meeting_offers")[0][0] == 0
        assert execute("SELECT count(*) FROM meeting_selections")[0][0] == 0
        migrate("downgrade", "d13026e9a033")
        assert (
            execute("SELECT id FROM calendar_slot_requests WHERE id='old-query'")[0][0]
            == "old-query"
        )
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(query(f'DROP DATABASE "{target.database}" WITH (FORCE)', admin=True))
