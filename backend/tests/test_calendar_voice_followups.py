"""User-supplied cricket voice sequence, with synthetic Google and approval only."""
# ruff: noqa: F811

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.actions import calendar_worker
from app.conversation import service
from app.db.models import ActionApproval, ActionAttempt, AssistantAction
from tests.test_calendar_creation import configured, ready, run, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool

CRICKET = {
    "title": "cricket",
    "date": {"kind": "relative", "offset_days": 1},
    "date_source": "tomorrow",
    "time": "17:00",
    "time_source": "5:00 p.m.",
}


@pytest.mark.parametrize("verb", ["make", "create"])
async def test_voice_creation_verbs_and_default_duration(configured, db_sessionmaker, verb):
    await ready(db_sessionmaker)
    request = turn(f"{verb} an event at 5:00 p.m. tomorrow for cricket")
    result = await run(db_sessionmaker, request, CRICKET)
    assert result["kind"] == "calendar_event"
    event = result["calendar_action"]["preview"]["event"]
    assert event["summary"] == "cricket"
    assert datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(
        event["start"]["dateTime"]
    ) == timedelta(minutes=30)
    assert "saved 30-minute duration" in result["text"]
    assert not any(c.method == "POST" for c in configured[0])


async def test_make_request_cannot_finish_with_unexecuted_retry_promise(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("make an event at 5:00 p.m. tomorrow for cricket")
    model = Model(
        tool("prepare_calendar_event", intent=None),
        tool(
            "respond",
            kind="message",
            text="I'm having trouble creating the event. "
            "Let me try a different approach to set up your cricket event for 5:00 p.m. tomorrow.",
        ),
        tool("prepare_calendar_event", **CRICKET),
    )
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert result["kind"] == "calendar_event" and len(model.contexts) == 3
    assert result["trace"][1]["reason"] == "calendar_preparation_required"


async def test_gratitude_and_closing_after_single_mocked_write(
    configured, db_sessionmaker, db_client, auth_headers
):
    await ready(db_sessionmaker)
    request = turn("create an event at 5:00 p.m. tomorrow for cricket")
    first = await run(db_sessionmaker, request, CRICKET)
    action = first["calendar_action"]
    path = f"/assistant/calendar-actions/{action['action_id']}/approve"
    approved = db_client.post(
        path,
        headers=auth_headers(1),
        json={
            "request_id": "synthetic-cricket-approval",
            "expected_version": action["version"],
            "payload_hash": action["payload_hash"],
        },
    )
    assert approved.status_code == 202
    assert await calendar_worker.run_once(db_sessionmaker, transport=configured[1])
    version = first["version"]
    # Replay the actual misrouting: a gratitude turn asks the event tool to
    # resume the old action. It must become plain acknowledgment, not a card.
    for text, decision in [
        ("thank you", tool("prepare_calendar_event", continue_previous=True)),
        (
            "no thank you",
            tool(
                "respond",
                kind="message",
                text="No problem! Is there anything else I can help you with?",
            ),
        ),
    ]:
        followup = turn(text, conversation_id=request.conversation_id, expected_version=version)
        result = await service.turn(1, followup, factory=db_sessionmaker, model=Model(decision))
        version = result["version"]
        assert result["kind"] == "message" and "calendar_action" not in result
        assert "?" not in result["text"] and "created" not in result["text"]
        replay = await service.turn(1, followup, factory=db_sessionmaker, model=Model())
        assert replay["text"] == result["text"] and "calendar_action" not in replay
    async with db_sessionmaker() as db:
        for cls in (AssistantAction, ActionApproval, ActionAttempt):
            assert await db.scalar(select(func.count()).select_from(cls)) == 1
    assert len([c for c in configured[0] if c.url.path.endswith("/events")]) == 1


async def test_gratitude_prefix_does_not_hide_new_event_request(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("thank you please make an event at 5:00 p.m. tomorrow for cricket")
    result = await run(db_sessionmaker, request, CRICKET)
    assert result["kind"] == "calendar_event"


async def test_new_creation_cannot_silently_revise_previous_title(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("create an event at 5:00 p.m. tomorrow for cricket")
    first = await run(db_sessionmaker, request, CRICKET)
    text = "make an event at 6:00 p.m. tomorrow for tennis"
    followup = turn(
        text, conversation_id=request.conversation_id, expected_version=first["version"]
    )
    # A model incorrectly continues the old event and supplies only its new time.
    model = Model(
        tool(
            "prepare_calendar_event", continue_previous=True, time="18:00", time_source="6:00 p.m."
        ),
        tool(
            "prepare_calendar_event",
            **{**CRICKET, "title": "tennis", "time": "18:00", "time_source": "6:00 p.m."},
        ),
    )
    result = await service.turn(1, followup, factory=db_sessionmaker, model=model)
    assert len(model.contexts) == 2
    assert result["calendar_action"]["preview"]["event"]["summary"] == "tennis"
    assert result["trace"][0]["reason"] == "calendar_new_goal_required"


async def test_complete_trailing_title_is_repaired_not_silently_shortened(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("make an event at 5:00 p.m. tomorrow for cricket practice")
    model = Model(
        tool("prepare_calendar_event", **CRICKET),
        tool("prepare_calendar_event", **{**CRICKET, "title": "cricket practice"}),
    )
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert len(model.contexts) == 2
    assert result["calendar_action"]["preview"]["event"]["summary"] == "cricket practice"
    assert result["trace"][0]["reason"] == "calendar_title_incomplete"


@pytest.mark.parametrize(
    "text, extra",
    [
        ("create an event at 5:00 p.m. tomorrow for cricket in Room B", {"location": "Room B"}),
        (
            "create an event at 5:00 p.m. tomorrow for cricket for 45 minutes",
            {"duration_phrase": "45 minutes"},
        ),
        ("create an event for cricket tomorrow at 5:00 p.m.", {}),
    ],
)
async def test_title_check_does_not_consume_following_fields(
    configured, db_sessionmaker, text, extra
):
    await ready(db_sessionmaker)
    result = await run(db_sessionmaker, turn(text), {**CRICKET, **extra})
    assert result["kind"] == "calendar_event"
    assert result["calendar_action"]["preview"]["event"]["summary"] == "cricket"
