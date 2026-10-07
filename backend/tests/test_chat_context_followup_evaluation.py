"""Offline rehearsal of second-budget setup/contracts; never a real-model result."""
# ruff: noqa: F811

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.assistant import worker
from app.calendar import permissions
from app.config import get_settings
from app.conversation import store
from app.db.models import ActionJob, AssistantAction, AssistantTask, Conversation
from tests.test_chat_context_evaluation_budget import Raw
from tests.test_conversation import Model, configured, tool  # noqa: F401
from tests.test_on_demand_gmail import Model as Generator
from tests.test_on_demand_gmail import setup  # noqa: F401
from tools.evaluate_chat_context import APPROVED_MODEL, BudgetExceeded
from tools.evaluate_chat_context_followup import (
    FollowupBudget,
    install_calendar_fake,
    prepare_calendar_account,
    record_state,
    scenarios,
    seed_cases,
    step,
)


def test_followup_ledger_is_separate_and_deadline_survives_restart(tmp_path):
    path = tmp_path / "second.json"
    first = tmp_path / "first.json"
    first.write_text('{"calls": ["exhausted-first-batch"]}')
    budget = FollowupBudget(path)
    budget.scenario = scenarios()[0]["id"]
    budget.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    budget.close()
    identity = tmp_path / "second.json.identity.json"
    saved = json.loads(identity.read_text())
    saved["started_unix"] -= 901
    identity.write_text(json.dumps(saved))
    resumed = FollowupBudget(path)
    try:
        assert len(resumed.calls) == 1
        with pytest.raises(BudgetExceeded, match="time budget"):
            resumed.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    finally:
        resumed.close()
    assert first.read_text() == '{"calls": ["exhausted-first-batch"]}'


async def test_second_allocation_rehearsal_completes_with_no_aws(
    configured, db_sessionmaker, monkeypatch
):
    from botocore.client import BaseClient

    def no_aws(*args, **kwargs):
        raise AssertionError("Offline rehearsal cannot call AWS")

    monkeypatch.setattr(BaseClient, "_make_api_call", no_aws)
    monkeypatch.setenv("CALENDAR_WRITES_ENABLED", "true")
    monkeypatch.setenv("WRITE_PILOT_USER_IDS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(permissions, "get_session_factory", lambda: db_sessionmaker)
    await prepare_calendar_account(db_sessionmaker)
    calendar_calls = []
    install_calendar_fake(monkeypatch, calendar_calls)
    seeds = await seed_cases(db_sessionmaker)
    draft_case, calendar_case, saved_case = scenarios()
    previous = seeds[0]["previous"]
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, previous["conversation_id"]))
        alex_id = state["email_draft_goal"]["goal_id"]
    text = draft_case["turns"][0]
    casey = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "prepare_email_draft",
                recipient="Casey",
                request_source=text,
            )
        ),
    )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, casey["conversation_id"]))
        casey_id = state["email_draft_goal"]["goal_id"]
    assert alex_id != casey_id
    previous = casey
    for text, recipient, goal_id, purpose, body in (
        (
            draft_case["turns"][1],
            "Alex",
            alex_id,
            "ask whether the sapphire crate has arrived",
            "Hi Alex, has the sapphire crate arrived?",
        ),
        (
            draft_case["turns"][2],
            "Casey",
            casey_id,
            "thank them for the map",
            "Hi Casey, thanks for the map.",
        ),
    ):
        previous = await step(
            db_sessionmaker,
            previous,
            text,
            Model(
                tool("select_conversation_goal", goal_id=goal_id, source=text),
                tool(
                    "prepare_email_draft",
                    continue_previous=True,
                    request_source=text,
                    purpose=purpose,
                    draft={
                        "subject": "Quick note",
                        "body": body,
                        "unresolved_fields": [],
                        "sources": [],
                    },
                ),
            ),
        )
        assert previous["email_draft"]["recipient"] == recipient, previous
        assert body in previous["text"]
    saved = await record_state(db_sessionmaker, previous)
    assert len(saved["goals"]["goals"]) == 2

    calendar = await step(
        db_sessionmaker,
        None,
        calendar_case["turns"][0],
        Model(
            tool(
                "prepare_calendar_event",
                title="Quiet hour",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
                time="14:00",
                time_source="2 pm",
            )
        ),
    )
    assert calendar["kind"] == "calendar_event", json.dumps(calendar)
    revised = await step(
        db_sessionmaker,
        calendar,
        calendar_case["turns"][1],
        Model(
            tool(
                "prepare_calendar_event",
                continue_previous=True,
                changes=[
                    {"field": "time", "operation": "replace", "value": "15:00", "source": "3 pm"}
                ],
            )
        ),
    )
    assert revised["kind"] == "calendar_event", revised
    event = revised["calendar_action"]["preview"]["event"]
    local_start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    )
    assert event["summary"] == "Quiet hour" and local_start.hour == 15
    ended = await step(
        db_sessionmaker,
        revised,
        calendar_case["turns"][2],
        Model(
            tool(
                "respond",
                kind="message",
                text="You're welcome.",
            )
        ),
    )
    assert ended["kind"] == "message"

    async with db_sessionmaker() as db:
        task_count_before_resume = await db.scalar(select(func.count()).select_from(AssistantTask))
    text = saved_case["turns"][0]
    restored = await step(
        db_sessionmaker,
        seeds[2]["previous"],
        text,
        Model(
            tool("resume_conversation_task", turn_version=1, source=text),
            tool("respond", kind="message", text="Here is the earlier draft."),
        ),
    )
    inspected = await record_state(db_sessionmaker, restored)
    original = seeds[2]["saved_artifact"]
    assert restored["task_id"] == original["task_id"]
    assert inspected["task"]["artifact_id"] == original["artifact_id"]
    assert inspected["task"]["artifact"] == original["artifact"]
    assert inspected["state"]["calendar_event_request"]["arguments"]["title"] == "Focus"
    assert not await worker.run_once(db_sessionmaker, Generator())
    async with db_sessionmaker() as db:
        assert (
            await db.scalar(select(func.count()).select_from(AssistantTask))
            == task_count_before_resume
        )
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AssistantAction)
                .where(AssistantAction.state.in_(["approved", "running", "succeeded"]))
            )
            == 0
        )
    assert not any(c["method"] == "POST" and c["path"].endswith("events") for c in calendar_calls)
