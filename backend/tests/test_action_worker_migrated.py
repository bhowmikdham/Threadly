"""Worker transitions on actual Alembic guards, without any real provider call."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.actions import approval, gmail_sender, reconciliation, worker
from app.config import get_settings
from app.db.models import ActionAttempt, ActionJob, ArtifactRevision, AssistantAction, User
from tests.conftest import needs_pg
from tests.test_action_approval import approve_request, candidate
from tests.test_email_previews import accept
from tests.test_email_previews import request as preview_request
from tests.test_gmail_reconciliation import Sent


@needs_pg
def test_worker_on_migrated_guards(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_writes_enabled", True)
    url = make_url(get_settings().database_url)
    database = "threadly_worker_" + uuid4().hex
    target = url.set(database=database)

    async def admin(sql):
        connection = await asyncpg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    async def exercise():
        engine = create_async_engine(target)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def token(owner):
            return "synthetic"

        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"id": "accepted", "threadId": "t"})
        )
        try:
            async with factory.begin() as session:
                session.add(User(id=91, google_sub="worker-owner", email="owner@example.test"))
            first = await candidate(factory, 91)
            async with factory.begin() as session:
                await approval.approve(
                    session, 91, first.id, approve_request(first), execution_enabled=True
                )
            assert await worker.run_once(factory, transport=transport, token_loader=token)
            async with factory() as session:
                stored = await session.get(AssistantAction, first.id)
                assert stored.state == "succeeded" and stored.payload == first.payload
                assert (await session.get(ActionJob, first.id)).state == "done"
                completed = await session.scalar(
                    select(ActionAttempt).where(ActionAttempt.action_id == first.id)
                )
                draft = await session.get(ArtifactRevision, first.artifact_id)
            # A delayed competing completion cannot replace a confirmed terminal result.
            stale = worker.Dispatch(
                worker.Claim(first.id, 91, completed.lease_token),
                completed.id,
                completed.action_version + 1,
                {},
            )
            assert not await worker.finish(
                factory, stale, gmail_sender.Outcome("failed", "stale_failure")
            )
            async with factory() as session:
                stored = await session.get(AssistantAction, first.id)
                assert (
                    stored.state == "succeeded" and stored.result["gmail_message_id"] == "accepted"
                )
            second = await accept(factory, 91, draft, preview_request("second"))
            async with factory.begin() as session:
                await approval.approve(
                    session,
                    91,
                    second.id,
                    approve_request(second, request_id="approve-two"),
                    execution_enabled=True,
                )
            claim = await worker.claim_one(factory, transport=transport)
            dispatch = await worker.prepare(factory, claim, transport=transport)
            async with factory.begin() as session:
                (await session.get(ActionJob, second.id)).lease_expires_at = await session.scalar(
                    select(func.clock_timestamp())
                ) - timedelta(seconds=1)
            assert await worker.recover_one(factory)
            assert not await worker.finish(
                factory,
                dispatch,
                gmail_sender.Outcome("succeeded", "gmail_accepted", "sent-one", "thread-one"),
            )
            async with factory() as session:
                action = await session.get(AssistantAction, second.id)
                attempt = await session.scalar(
                    select(ActionAttempt).where(ActionAttempt.action_id == second.id)
                )
                assert action.state == "outcome_unknown" and action.result is None
                assert attempt.evidence["late_response"]["message_id"] == "sent-one"
                assert (await session.get(ActionJob, second.id)).kind == "reconcile"
            assert not await worker.run_once(factory, transport=transport, token_loader=token)
            monkeypatch.setattr(get_settings(), "email_reconciliation_enabled", True)
            monkeypatch.setattr(get_settings(), "email_writes_enabled", False)
            sent = Sent(action.payload, attempt.dispatch_intent_at)
            assert await reconciliation.run_once(
                factory, transport=httpx.MockTransport(sent), token_loader=token
            )
            async with factory() as session:
                action = await session.get(AssistantAction, second.id)
                recovered = await session.get(ActionAttempt, attempt.id)
                assert action.state == recovered.state == "succeeded"
                assert action.result["gmail_message_id"] == "sent-one"
                assert recovered.evidence["late_response"]["message_id"] == "sent-one"
                assert recovered.evidence["reconciliation"]["rounds"] == 1
            assert not await reconciliation.run_once(
                factory, transport=httpx.MockTransport(sent), token_loader=token
            )
            assert len(sent.calls) == 3
        finally:
            await engine.dispose()

    asyncio.run(admin(f'CREATE DATABASE "{database}"'))
    try:
        for command in [("upgrade", "head"), ("check",)]:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", *command],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "DATABASE_URL": target.render_as_string(hide_password=False)},
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
        asyncio.run(exercise())
    finally:
        asyncio.run(admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
