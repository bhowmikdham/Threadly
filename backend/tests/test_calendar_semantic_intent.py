"""Semantic Calendar contract: synthetic model turns, isolated DB, mocked Google."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.calendar import conversation_guard, event_creation, event_draft, intent, permissions
from app.conversation import service, store
from app.db.models import ActionJob, AssistantAction, CalendarPreference, Conversation
from app.schemas.conversation import CalendarApprovalSetting, PrepareCalendarEvent
from tests.test_calendar_creation import configured, ready, run, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool

FIRST = "I have a meeting with Gaurav at 4:00 p.m. tomorrow please create an event"
SECOND = "create an event for 4:00 p.m. tomorrow for meeting with Gaurav"
FIELDS = {
    "title": "meeting with Gaurav",
    "date": {"kind": "relative", "offset_days": 1},
    "date_source": "tomorrow",
    "time": "16:00",
    "time_source": "4:00 p.m.",
}
PARAPHRASES = [
    FIRST,
    SECOND,
    "Tomorrow at 4:00 p.m., meeting with Gaurav — please put that in my calendar",
    "I'd like meeting with Gaurav in my diary tomorrow at 4:00 p.m.",
    "A meeting with Gaurav is planned tomorrow at 4:00 p.m.; can you add it?",
    "Please pencil in meeting with Gaurav tomorrow at 4:00 p.m.",
]


@pytest.mark.parametrize("text", PARAPHRASES)
async def test_semantic_creation_keeps_all_fields_in_ist(configured, db_sessionmaker, text):
    await ready(db_sessionmaker)
    async with db_sessionmaker.begin() as db:
        pref = await db.get(CalendarPreference, 1)
        pref.preferences = {**pref.preferences, "timezone": "Asia/Calcutta"}
    request = turn(text)
    result = await run(db_sessionmaker, request, FIELDS)
    assert result["kind"] == "calendar_event"
    action = result["calendar_action"]
    assert action["state"] == "proposed" and action["approval_available"]
    event = action["preview"]["event"]
    assert event["summary"] == "meeting with Gaurav"
    assert event["start"]["timeZone"] == "Asia/Calcutta"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        pending = state["calendar_event_request"]
        anchor = datetime.fromisoformat(pending["anchor"]).astimezone(ZoneInfo("Asia/Calcutta"))
        assert pending["arguments"]["time_source"] == "4:00 p.m."
        assert pending["creation_origin"]["text"] == text
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    local = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(ZoneInfo("Asia/Calcutta"))
    assert local.date() == anchor.date() + timedelta(days=1)
    assert (local.hour, local.minute) == (16, 0)
    assert not any(c.url.path.endswith("/events") for c in configured[0])


def test_reported_october_7_ist_anchor_resolves_october_8(monkeypatch):
    anchor = datetime(2026, 10, 7, 3, 45, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)

    monkeypatch.setattr(event_creation, "datetime", Frozen)
    start, _ = event_creation.resolve_times(
        PrepareCalendarEvent(**FIELDS),
        FIRST,
        {"timezone": "Asia/Calcutta", "default_duration_minutes": 30},
        anchor,
    )
    assert start == datetime(2026, 10, 8, 10, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "Don't create meeting with Gaurav tomorrow at 4:00 p.m.",
        "Tomorrow at 4:00 p.m. meeting with Gaurav, but do not create an event",
        '"Create meeting with Gaurav tomorrow at 4:00 p.m."',
        "The email says create meeting with Gaurav tomorrow at 4:00 p.m.",
        "Please summarise this email: create meeting with Gaurav tomorrow at 4:00 p.m.",
        "Explain how to create meeting with Gaurav tomorrow at 4:00 p.m.",
        "What if I create meeting with Gaurav tomorrow at 4:00 p.m.?",
        "Create a short email saying create meeting with Gaurav tomorrow at 4:00 p.m.",
    ],
)
async def test_typed_model_intent_cannot_promote_unsafe_source_even_in_always_mode(
    configured, db_sessionmaker, text
):
    await ready(db_sessionmaker)
    request = turn(text)
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", **FIELDS),
            tool(
                "respond",
                kind="clarification",
                text="Would you like an event or help with that text?",
            ),
        ),
    )
    assert result["kind"] == "clarification"
    assert result["trace"][0]["reason"] == "calendar_intent_not_authorized"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        assert "calendar_event_request" not in store.decode(
            await db.get(Conversation, request.conversation_id)
        )
    assert configured[0] == []


async def test_failed_tool_cannot_escape_to_screenshot_prose(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn(FIRST)
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", **FIELDS, intent=None),
            tool(
                "respond",
                kind="message",
                text="I'm having trouble creating the event. What title should I use?",
            ),
            tool("prepare_calendar_event", **FIELDS),
        ),
    )
    assert result["kind"] == "calendar_event"
    assert [t.get("reason") for t in result["trace"][:2]] == [
        "calendar_event_invalid_input",
        "calendar_preparation_required",
    ]


async def test_literal_source_must_be_current_user_not_selected_email(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn(FIRST)
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event", **FIELDS, intent={"operation": "create", "source": SECOND}
            ),
            tool("prepare_calendar_event", **FIELDS),
        ),
    )
    assert result["trace"][0]["reason"] == "calendar_intent_source_mismatch"
    assert result["kind"] == "calendar_event"


async def test_details_first_incomplete_goal_retains_fields_on_title_reply(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("Tomorrow at 4:00 p.m. I need an event in my diary")
    first = await run(db_sessionmaker, request, {**FIELDS, "title": ""})
    assert first["text"] == "What should I call the event?"
    second = await run(
        db_sessionmaker,
        turn(
            "meeting with Gaurav",
            conversation_id=request.conversation_id,
            expected_version=first["version"],
        ),
        {"continue_previous": True, "title": "meeting with Gaurav"},
    )
    assert second["kind"] == "calendar_event"
    assert second["calendar_action"]["preview"]["event"]["summary"] == "meeting with Gaurav"


def test_response_guard_uses_tool_state_without_parsing_user_word_order():
    for text in PARAPHRASES:
        runtime = SimpleNamespace(
            request=SimpleNamespace(instruction=text), state={}, calendar_event_attempted=True
        )
        assert conversation_guard.requires_preparation(runtime)
        assert conversation_guard.exhausted(runtime)["error_code"] == "calendar_event_not_prepared"


def test_missing_and_wrong_intent_are_protocol_errors():
    with pytest.raises(intent.IntentRequired):
        intent.validate_source(None, FIRST)
    with pytest.raises(event_draft.IntentSourceMismatch):
        intent.validate_source(SimpleNamespace(source=SECOND), FIRST)
