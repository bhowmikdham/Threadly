"""Event extraction repair with scripted model decisions and isolated fake providers."""
# ruff: noqa: F811

import json

import pytest
from sqlalchemy import func, select

from app.conversation import engine, service, store
from app.db.models import ActionApproval, ActionJob, AssistantAction, Conversation
from tests.test_calendar_creation import configured, ready, run, turn  # noqa: F401
from tests.test_calendar_intent_source import ObservedModel
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import tool

TEXT = "could you book me an event for 7 pm tomorrow"
FIELDS = {
    "date": {"kind": "relative", "offset_days": 1},
    "date_source": "tomorrow",
    "time": "19:00",
    "time_source": "7 pm",
}


@pytest.mark.parametrize(
    "bad,field,reason",
    [
        ({"title": "Private invented title"}, "title", "source"),
        ({"date_source": "yesterday"}, "date", "source"),
        ({"time_source": "7:00 p.m."}, "time", "source"),
        ({"time": "07:00"}, "time", "interpretation"),
        ({"time": "25:99"}, "time", "interpretation"),
        ({"duration_phrase": "45 minutes"}, "duration_phrase", "source"),
        ({"location": "Private location"}, "location", "source"),
        ({"description": "Private description"}, "description", "source"),
        ({"calendar_name": "Private calendar"}, "calendar_name", "source"),
        ({"attendees": ["private@example.test"]}, "attendees", "source"),
    ],
)
async def test_field_failure_repairs_then_asks_only_missing_title(
    configured, db_sessionmaker, caplog, bad, field, reason
):
    await ready(db_sessionmaker)
    model = ObservedModel(
        tool("prepare_calendar_event", **(FIELDS | bad)),
        tool("prepare_calendar_event", **FIELDS),
    )
    request = turn(TEXT)
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert result["text"] == "What should I call the event?"
    assert result["trace"][0] == {
        "tool": "prepare_calendar_event",
        "status": "invalid",
        "reason": f"calendar_field_{reason}_mismatch",
        "field": field,
    }
    assert len(model.contexts) == 2
    feedback = model.messages[-1]["content"][0]["toolResult"]
    assert feedback["status"] == "error"
    assert feedback["content"][0]["json"]["field"] == field
    # Values occur in the private model input, never in feedback, trace, or application logs.
    diagnostics = json.dumps([feedback, result["trace"]]) + caplog.text
    for value in bad.values():
        for item in value if isinstance(value, list) else [value]:
            assert item not in diagnostics
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        args = state["calendar_event_request"]["arguments"]
        assert args["title"] == "" and args["time"] == "19:00"
        assert args["date"]["offset_days"] == 1 and args["date_source"] == "tomorrow"
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
    assert configured[0] == []


async def test_exact_titleless_query_then_title_retains_grounded_fields(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn(TEXT)
    first = await run(db_sessionmaker, request, FIELDS)
    assert first["text"] == "What should I call the event?"
    follow = turn("cricket", conversation_id=request.conversation_id, expected_version=1)
    second = await run(db_sessionmaker, follow, {"continue_previous": True, "title": "cricket"})
    event = second["calendar_action"]["preview"]["event"]
    assert event["summary"] == "cricket"
    assert second["calendar_action"]["state"] == "proposed"
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_invalid_schema_feedback_is_event_specific_and_sanitized(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    model = ObservedModel(
        tool("prepare_calendar_event", **FIELDS, title={"private_key": "private value"}),
        tool("prepare_calendar_event", **FIELDS),
    )
    result = await service.turn(1, turn(TEXT), factory=db_sessionmaker, model=model)
    assert result["text"] == "What should I call the event?"
    assert result["trace"][0]["reason"] == "calendar_event_invalid_input"
    assert result["trace"][0]["fields"] == ["title"]
    feedback = model.messages[-1]["content"][0]["toolResult"]
    assert "read_email" not in json.dumps(feedback)
    assert "private" not in json.dumps([feedback, result["trace"]])


async def test_unrepaired_fields_exhaust_budget_without_candidate(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn(TEXT)
    model = ObservedModel(
        *[
            tool("prepare_calendar_event", **FIELDS, title=f"invented-{i}")
            for i in range(engine.MAX_CALLS)
        ]
    )
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert len(model.contexts) == engine.MAX_CALLS
    assert result["error_code"] == "calendar_event_not_prepared"
    assert all(t["reason"] == "calendar_field_source_mismatch" for t in result["trace"]), result[
        "trace"
    ]
    assert "Calendar read" not in result["text"]
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        pending = state["calendar_event_request"]
        assert pending["arguments"]["title"] == ""
        assert pending["arguments"]["time"] == "19:00"
        assert pending["arguments"]["date"] == {"kind": "relative", "offset_days": 1, "days": 1}
        assert "invented-" not in str(pending)
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
    assert configured[0] == []


async def test_new_cricket_request_repairs_without_reusing_prior_approval(
    configured, db_sessionmaker, db_client, auth_headers
):
    await ready(db_sessionmaker)
    first_request = turn("Create Focus at 7 pm tomorrow")
    first = await run(db_sessionmaker, first_request, FIELDS | {"title": "Focus"})
    old = first["calendar_action"]
    approval = db_client.post(
        f"/assistant/calendar-actions/{old['action_id']}/approve",
        headers=auth_headers(1),
        json={
            "request_id": "synthetic-old-approval",
            "expected_version": old["version"],
            "payload_hash": old["payload_hash"],
        },
    )
    assert approval.status_code == 202
    text = "could you book 7:00 p.m. tomorrow for cricket"
    request = turn(text, conversation_id=first_request.conversation_id, expected_version=1)
    correct = FIELDS | {"title": "cricket", "time_source": "7:00 p.m."}

    class CheckPriorApproval(ObservedModel):
        async def decide(self, system, messages, tools):
            async with db_sessionmaker() as db:
                assert (await db.get(AssistantAction, old["action_id"])).state == "approved"
            return await super().decide(system, messages, tools)

    model = CheckPriorApproval(
        tool("prepare_calendar_event", continue_previous=True, **correct),
        tool("prepare_calendar_event", **(correct | {"title": "Focus"})),
        tool("prepare_calendar_event", **correct),
    )
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert result["trace"][0]["reason"] == "calendar_new_goal_required"
    assert result["trace"][1]["reason"] == "calendar_title_incomplete"
    new = result["calendar_action"]
    assert new["action_id"] != old["action_id"] and new["state"] == "proposed"
    assert new["preview"]["event"]["summary"] == "cricket"
    async with db_sessionmaker() as db:
        # A new independent event retains the prior event's own approval. It
        # must not borrow that approval or silently cancel the other goal.
        assert (await db.get(AssistantAction, old["action_id"])).state == "approved"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ActionApproval)
                .where(ActionApproval.action_id == new["action_id"])
            )
            == 0
        )
        assert await db.get(ActionJob, new["action_id"]) is None
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["goal_id"] == request.request_id
    assert not any(c.url.path.endswith("/events") for c in configured[0])
