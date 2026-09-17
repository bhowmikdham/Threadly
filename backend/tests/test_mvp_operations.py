"""Operations control must remain owner-scoped and preserve recovery sources."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.assistant import worker
from app.config import Settings, get_settings
from app.db.models import AssistantTask, ContextSnapshot
from app.operations import retention, status
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_mvp_workflows import Model, body

# ruff: noqa: F811


def test_kill_switch_checks_every_requested_step(monkeypatch):
    monkeypatch.setattr(get_settings(), "assistant_disabled_intents", "reply")
    claim = SimpleNamespace(
        workflow_input={"request": {"operations": ["summary", "schedule", "draft_reply"]}}
    )
    assert status.blocked_intents(claim) == {"reply"}
    for setting in ({"assistant_disabled_intents": "summry"}, {"write_pilot_user_ids": "everyone"}):
        with pytest.raises(ValidationError):
            Settings(**setting)


@needs_pg
async def test_cleanup_preserves_referenced_contexts_and_owner_status(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/workflow-requests",
        headers=auth_headers(1),
        json=body(
            context, operations=["plan"], schedule=None, draft_options=None, summary_in_draft=False
        ),
    )
    assert response.status_code == 202, response.text
    async with db_sessionmaker.begin() as session:
        source = await session.get(ContextSnapshot, context)
        source.created_at = datetime.now(UTC) - timedelta(days=30)
        session.add(
            ContextSnapshot(
                id="orphan",
                user_id=1,
                thread_id=source.thread_id,
                source_hash=source.source_hash,
                payload=source.payload,
                created_at=source.created_at,
            )
        )
    dry = await retention.cleanup(db_sessionmaker)
    assert dry["counts"]["orphan_context_snapshots"] == 1
    async with db_sessionmaker() as session:
        assert await session.get(ContextSnapshot, "orphan")
    await retention.cleanup(db_sessionmaker, apply=True)
    async with db_sessionmaker() as session:
        assert await session.get(ContextSnapshot, context)
        assert await session.get(ContextSnapshot, "orphan") is None
        assert (await status.snapshot(session, 1))["tasks"] == {"queued": 1}
        assert (await status.snapshot(session, 2))["tasks"] == {}


@needs_pg
async def test_kill_switch_during_generation_prevents_publication(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    context = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/workflow-requests",
        headers=auth_headers(1),
        json=body(
            context, operations=["plan"], schedule=None, draft_options=None, summary_in_draft=False
        ),
    )

    async def stop():
        monkeypatch.setattr(get_settings(), "assistant_disabled_intents", "plan_schedule")

    await worker.run_once(db_sessionmaker, Model(stop))
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, response.json()["task_id"])
        assert task.state == "failed" and task.final_artifact_id is None
        assert task.error_code == "intent_disabled"
