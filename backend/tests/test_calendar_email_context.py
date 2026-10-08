"""Email facts cross into a proposed event; model and Google are deterministic fakes."""
# ruff: noqa: F811

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.assistant import source_data
from app.calendar import permissions
from app.config import get_settings
from app.conversation import service, store
from app.db.models import (
    ActionApproval,
    ActionJob,
    AssistantAction,
    CalendarPreference,
    Conversation,
)
from app.schemas.conversation import CalendarApprovalSetting
from tests.test_calendar_creation import turn
from tests.test_conversation import Model, tool
from tests.test_meeting_email_event import configured, setup  # noqa: F401
from tests.test_shared_mail_context import pinned


@pytest.fixture()
async def inspection(configured, monkeypatch, db_sessionmaker):
    monkeypatch.setenv("CONVERSATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    async with db_sessionmaker.begin() as db:
        pref = await db.get(CalendarPreference, 1)
        pref.preferences = {
            **pref.preferences,
            "timezone": "Australia/Melbourne",
            "default_duration_minutes": 30,
        }
    today = datetime.now(UTC).date()
    day = today.replace(day=15)
    if day <= today:
        day = (today.replace(day=1) + timedelta(days=32)).replace(day=15)
    quote = (
        f"The next property inspection is scheduled for {day:%d/%m/%Y} 8:30 AM at 20 Example St."
    )
    configured[0].text = quote
    fields = {
        "title": "Property inspection",
        "date": {"kind": "absolute", "start": day.isoformat()},
        "date_source": f"{day:%d/%m/%Y}",
        "time": "08:30",
        "time_source": "8:30 AM",
        "location": "20 Example St",
        "email_source": {
            "reference": "selected",
            "ambiguity": "none",
            "event_quote": quote,
            "fields": [
                {"field": field, "quote": value}
                for field, value in [
                    ("title", "property inspection"),
                    ("date", f"{day:%d/%m/%Y}"),
                    ("time", "8:30 AM"),
                    ("location", "20 Example St"),
                ]
            ],
        },
    }
    return configured, fields, quote


async def prepare(factory, fields, *, summary=False, always=False):
    async with source_data.source_scope():
        capture = await pinned(factory)
    request = turn(
        "create an event from this", context_snapshot_id=capture.id, timezone="Australia/Melbourne"
    )
    if always:
        await permissions.set_mode(
            1,
            request.conversation_id,
            CalendarApprovalSetting(mode="always", expected_version=0),
            factory=factory,
        )
    if summary:
        request.instruction = "Summarise this thread."
        async with source_data.source_scope():
            result = await service.turn(
                1,
                request,
                factory=factory,
                model=Model(
                    tool("read_email", reference="selected", scope="selected_message"),
                    tool(
                        "respond",
                        kind="message",
                        text="The property inspection is at 8:30 AM.",
                        evidence=[{"reference": "selected", "quote": "8:30 AM"}],
                    ),
                ),
            )
        request = turn(
            "can you create an event for that",
            conversation_id=request.conversation_id,
            expected_version=result["version"],
            timezone="Australia/Melbourne",
        )
    async with source_data.source_scope():
        result = await service.turn(
            1,
            request,
            factory=factory,
            model=Model(
                tool("read_email", reference="selected", scope="selected_message"),
                tool("prepare_calendar_event", **fields),
            ),
        )
    return request, result


@pytest.mark.parametrize("summary", [False, True])
@pytest.mark.parametrize("always", [False, True])
async def test_email_context_prefills_review_without_redundant_questions(
    inspection, db_sessionmaker, summary, always
):
    configured, fields, quote = inspection
    request, result = await prepare(db_sessionmaker, fields, summary=summary, always=always)
    assert result["kind"] == "calendar_event", result
    action = result["calendar_action"]
    event = action["preview"]["event"]
    assert action["state"] == "proposed"
    assert action["authorization"] == "separate_exact_event_approval"
    assert event["summary"] == "Property inspection"
    assert event["location"] == "20 Example St"
    assert event["start"]["timeZone"] == "Australia/Melbourne"
    assert "30-minute" in result["text"] and "Australia/Melbourne" in result["text"]
    assert not event["attendees"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ActionApproval)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["field_provenance"]["date"]["kind"] == "email"
        assert quote not in json.dumps(state)
    async with source_data.source_scope():
        replay = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert replay["calendar_action_id"] == action["action_id"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
    assert not any(c.url.path.endswith("/events") for c in configured[1])
