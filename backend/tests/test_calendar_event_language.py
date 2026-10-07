"""Spoken Calendar requests and truthful outcomes; isolated DB and fake providers only."""

# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.calendar import conversation_guard, event_choices, event_creation, event_draft, permissions
from app.calendar.time_resolution import parse_clock
from app.conversation import engine, service
from app.db.models import ActionJob, AssistantAction, Conversation
from app.schemas.conversation import CalendarApprovalSetting, PrepareCalendarEvent
from tests.test_calendar_creation import ARGS, configured, ready, run, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool


@pytest.mark.parametrize("source", ["2 p.m.", "2 PM", "2 p. m.", "2pm", "14:00"])
def test_spoken_clock_normalizes_without_changing_the_time(source):
    assert parse_clock(source) == [840]


@pytest.mark.parametrize("source", ["2 a.m.", "12 a.m.", "12 p.m."])
def test_meridiem_is_not_lost(source):
    assert parse_clock(source) == [{"2 a.m.": 120, "12 a.m.": 0, "12 p.m.": 720}[source]]


@pytest.mark.parametrize("source", ["13 p.m.", "2 p.m. tomorrow", "2 pm am", "24:00"])
def test_invalid_clock_is_rejected(source):
    with pytest.raises(ValueError):
        parse_clock(source)


@pytest.mark.parametrize(
    "instruction,title,clock,day",
    [
        (
            "could you please book 2 p.m. tomorrow for doctor's appointment?",
            "doctor's appointment",
            "2 p.m.",
            "tomorrow",
        ),
        ("book 2 pm tmrw for doctors appointment", "doctors appointment", "2 pm", "tmrw"),
        (
            "can you book me 2 pm for tmrw for doctors appointment",
            "doctors appointment",
            "2 pm",
            "tmrw",
        ),
        ("Book 14:00 tomorrow for Focus", "Focus", "14:00", "tomorrow"),
    ],
)
@pytest.mark.parametrize("mode", ["ask", "always"])
async def test_time_first_request_keeps_supplied_slots(
    configured, db_sessionmaker, instruction, title, clock, day, mode
):
    await ready(db_sessionmaker)
    request = turn(instruction)
    if mode == "always":
        await permissions.set_mode(
            1,
            request.conversation_id,
            CalendarApprovalSetting(mode=mode, expected_version=0),
            factory=db_sessionmaker,
        )
    result = await run(
        db_sessionmaker, request, {**ARGS, "title": title, "time_source": clock, "date_source": day}
    )
    assert result["kind"] == "calendar_event"
    action = result["calendar_action"]
    assert action["state"] == ("proposed" if mode == "ask" else "approved")
    event = action["preview"]["event"]
    assert event["summary"] == title
    local = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    )
    assert local.hour == 14 and local.minute == 0
    assert result["text"] != "Your event was created."
    assert not any(c.url.path.endswith("/events") for c in configured[0])


@pytest.mark.parametrize(
    "wrong",
    [
        tool("respond", kind="message", text="Done!"),
        tool(
            "respond",
            kind="message",
            text="I'm having trouble creating the calendar event. Let me try again.",
        ),
        tool("respond", kind="message", text="Your appointment is booked."),
        tool("respond", kind="clarification", text="What date and time should I use?"),
        tool("prepare_workflow", intent="plan_schedule"),
        {
            "role": "assistant",
            "content": tool("prepare_workflow", intent="plan_schedule")["content"]
            + tool("prepare_workflow", intent="other")["content"],
        },
    ],
)
async def test_creation_response_requires_an_actual_event_candidate(
    configured, db_sessionmaker, wrong
):
    await ready(db_sessionmaker)
    request = turn("book 2 pm tmrw for doctors appointment")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", intent=None),
            wrong,
            tool(
                "prepare_calendar_event",
                **{**ARGS, "title": "doctors appointment", "time_source": "2 pm"},
            ),
        ),
    )
    assert result["kind"] == "calendar_event"
    assert result["calendar_action"]["state"] == "proposed"
    assert result["trace"][1]["reason"] == "calendar_preparation_required"
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_empty_followup_defaults_do_not_erase_saved_slots(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("Book 2 p.m. tomorrow")
    first = await run(
        db_sessionmaker,
        request,
        {**ARGS, "title": "", "time_source": "2 p.m.", "date_source": "tomorrow"},
    )
    assert first["text"] == "What should I call the event?"
    followup = turn(
        "doctor's appointment", conversation_id=request.conversation_id, expected_version=1
    )
    fields = PrepareCalendarEvent(continue_previous=True, title="doctor's appointment").model_dump(
        mode="json"
    )
    result = await run(db_sessionmaker, followup, fields)
    event = result["calendar_action"]["preview"]["event"]
    assert event["summary"] == "doctor's appointment"
    assert (
        datetime.fromisoformat(event["start"]["dateTime"])
        .astimezone(ZoneInfo("Australia/Melbourne"))
        .hour
        == 14
    )


def test_tomorrow_uses_melbourne_day_across_utc_midnight(monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 5, 13, 5, tzinfo=UTC)

    monkeypatch.setattr(event_creation, "datetime", Frozen)
    args = PrepareCalendarEvent(
        **{
            **ARGS,
            "title": "doctors appointment",
            "time_source": "2 p.m.",
            "date_source": "tomorrow",
        }
    )
    start, _ = event_creation.resolve_times(
        args,
        "Book 2 p.m. tomorrow for doctors appointment",
        {"timezone": "Australia/Melbourne", "default_duration_minutes": 30},
        Frozen.now(),
    )
    # At the saved anchor Melbourne is already Tuesday 6 October; tomorrow is Wednesday.
    assert start == datetime(2026, 10, 7, 3, tzinfo=UTC)


async def test_exhausted_creation_completes_turn_and_allows_next_read(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("book 2 pm tmrw for doctors appointment")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", intent=None),
            *[tool("respond", kind="message", text="Done!") for _ in range(engine.MAX_CALLS - 1)]
        ),
    )
    assert result["error_code"] == "calendar_event_not_prepared" and result["version"] == 1
    assert result["kind"] == "message" and not result.get("calendar_action_id")
    async with db_sessionmaker() as db:
        row = await db.get(Conversation, request.conversation_id)
        assert row.pending_request_id is None and row.lease_id is None
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    next_request = turn(
        "am i free at 2 pm tmrw?", conversation_id=request.conversation_id, expected_version=1
    )
    checked = await service.turn(
        1,
        next_request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "check_time_availability",
                subject="self",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                at_time="14:00",
                at_time_source="2 pm",
            )
        ),
    )
    assert (
        checked["version"] == 2
        and checked["calendar_tools"]["operation"] == "check_time_availability"
    )
    assert (
        datetime.fromisoformat(checked["calendar_tools"]["start"])
        .astimezone(ZoneInfo("Australia/Melbourne"))
        .hour
        == 14
    )
    assert checked["calendar_tools"]["duration_minutes"] == 30
    assert not any(c.url.path.endswith("/events") for c in configured[0])
    # Replaying the failed issued key is its original terminal failure, not a second write.
    replay = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert replay["error_code"] == "calendar_event_not_prepared"


@pytest.mark.parametrize(
    "state", ["proposed", "approved", "outcome_unknown", "failed", "succeeded"]
)
async def test_status_followup_uses_durable_action_state(configured, db_sessionmaker, state):
    await ready(db_sessionmaker)
    request = turn()
    first = await run(db_sessionmaker, request)
    aid = first["calendar_action_id"]
    async with db_sessionmaker.begin() as db:
        action = await db.get(AssistantAction, aid)
        action.state = state
    followup = turn(
        "did you create it?", conversation_id=request.conversation_id, expected_version=1
    )
    result = await service.turn(
        1,
        followup,
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="Done! Your event was created.")),
    )
    assert result["calendar_action_id"] == aid and result["calendar_action"]["state"] == state
    assert (result["text"] == "Your event was created.") == (state == "succeeded")
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_unbacked_completion_claim_is_not_displayed(configured, db_sessionmaker):
    result = await service.turn(
        1,
        turn("Did you create the event?"),
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="Your event was created.")),
    )
    assert result["text"] == "I don't have a confirmed Calendar event for this request yet."
    assert not result.get("calendar_action_id")


@pytest.mark.parametrize(
    "instruction",
    [
        "Please summarise this email: book 2 pm tmrw for doctors appointment",
        "Create a short email saying book 2 pm tmrw for doctors appointment",
        "Book 2 pm tmrw for a summary",
    ],
)
async def test_time_first_form_cannot_promote_source_prose(
    configured, db_sessionmaker, instruction
):
    await ready(db_sessionmaker)
    request = turn(instruction)
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
            tool(
                "prepare_calendar_event",
                **{**ARGS, "title": "doctors appointment", "time_source": "2 pm"},
            ),
            tool("respond", kind="clarification", text="What should I do with that text?"),
        ),
    )
    assert result["kind"] == "clarification"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_time_first_event_is_confirmed_only_after_mock_provider_insert(
    configured, db_sessionmaker
):
    from app.actions import calendar_worker

    await ready(db_sessionmaker)
    request = turn("can you book me 2 pm for tmrw for doctors appointment")
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    result = await run(
        db_sessionmaker, request, {**ARGS, "title": "doctors appointment", "time_source": "2 pm"}
    )
    assert result["calendar_action"]["state"] == "approved"
    assert await calendar_worker.run_once(db_sessionmaker, transport=configured[1])
    result = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert result["calendar_action"]["state"] == "succeeded"
    assert result["text"] == "Your event was created."
    assert len([c for c in configured[0] if c.url.path.endswith("/events")]) == 1


async def test_failed_followup_preserves_pending_slots_for_same_chat(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("Book 2 p.m. tomorrow")
    await run(
        db_sessionmaker,
        request,
        {**ARGS, "title": "", "time_source": "2 p.m.", "date_source": "tomorrow"},
    )
    answer = turn("Focus", conversation_id=request.conversation_id, expected_version=1)
    failed = await service.turn(
        1,
        answer,
        factory=db_sessionmaker,
        model=Model(
            *[tool("respond", kind="message", text="Done!") for _ in range(engine.MAX_CALLS)]
        ),
    )
    assert failed["error_code"] == "calendar_event_not_prepared"
    retry = turn("Focus", conversation_id=request.conversation_id, expected_version=2)
    result = await run(db_sessionmaker, retry, {"continue_previous": True, "title": "Focus"})
    assert result["calendar_action"]["preview"]["event"]["summary"] == "Focus"


def test_omitted_dotted_clock_is_not_silently_dropped():
    args = PrepareCalendarEvent(
        title="Focus", date={"kind": "relative", "offset_days": 1}, date_source="tomorrow"
    )
    with pytest.raises(ValueError):
        event_creation.source_fields(args, "Create an event called Focus tomorrow 2 p.m.")


@pytest.mark.parametrize("verb", ["book", "schedule", "reserve", "create"])
@pytest.mark.parametrize("time_first", [True, False])
async def test_equivalent_booking_forms_produce_same_ask_payload(
    configured, db_sessionmaker, verb, time_first
):
    await ready(db_sessionmaker)
    text = f"could you please {verb} " + (
        "2 pm tmrw for Focus" if time_first else "Focus at 2 pm tmrw"
    )
    result = await run(db_sessionmaker, turn(text), {**ARGS, "time_source": "2 pm"})
    event = result["calendar_action"]["preview"]["event"]
    assert event["summary"] == "Focus"
    assert (
        datetime.fromisoformat(event["start"]["dateTime"])
        .astimezone(ZoneInfo("Australia/Melbourne"))
        .hour
        == 14
    )
    assert result["calendar_action"]["state"] == "proposed"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_cited_email_event_fact_is_not_replaced_by_an_execution_claim():
    from types import SimpleNamespace

    from app.schemas.conversation import Respond

    runtime = SimpleNamespace(request=SimpleNamespace(instruction="Summarise this email"), state={})
    answer = Respond(
        kind="message",
        text="The meeting was scheduled for Tuesday.",
        evidence=[{"reference": "mail-1", "quote": "Meeting scheduled Tuesday"}],
    )
    assert await conversation_guard.respond(runtime, answer) is None
    assert not conversation_guard.status_question("Did you create it?", False)


@pytest.mark.parametrize("failure", ["missing_title", "tool_limit"])
async def test_new_unfinished_event_never_reports_an_older_booking_as_its_success(
    configured, db_sessionmaker, failure
):
    await ready(db_sessionmaker)
    first_request = turn()
    old = await run(db_sessionmaker, first_request)
    async with db_sessionmaker.begin() as db:
        action = await db.get(AssistantAction, old["calendar_action_id"])
        action.state = "succeeded"
    next_request = turn(
        "book 2 pm tmrw", conversation_id=first_request.conversation_id, expected_version=1
    )
    if failure == "missing_title":
        await run(db_sessionmaker, next_request, {**ARGS, "title": "", "time_source": "2 pm"})
    else:
        await service.turn(
            1,
            next_request,
            factory=db_sessionmaker,
            model=Model(
                tool("prepare_calendar_event", intent=None),
                *[
                    tool("respond", kind="message", text="Done!")
                    for _ in range(engine.MAX_CALLS - 1)
                ]
            ),
        )
    status = turn(
        "Did you create the event?",
        conversation_id=first_request.conversation_id,
        expected_version=2,
    )
    result = await service.turn(
        1,
        status,
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="Your event was created.")),
    )
    assert "calendar_action_id" not in result and "confirmed" in result["text"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert (await db.get(AssistantAction, old["calendar_action_id"])).state == "succeeded"


async def test_reported_kelly_request_after_melbourne_midnight_is_not_a_calendar_read(
    configured, db_sessionmaker, monkeypatch
):
    from app.conversation import store

    # Proposal expiry uses PostgreSQL's real clock. Keep this midnight scenario
    # ahead of that clock instead of letting a fixed historical event expire.
    melbourne = ZoneInfo("Australia/Melbourne")
    async with db_sessionmaker() as db:
        database_now = await db.scalar(select(func.clock_timestamp()))
    local_day = database_now.astimezone(melbourne).date() + timedelta(days=1)
    local_midnight = datetime.combine(local_day, datetime.min.time(), tzinfo=melbourne)
    anchor = (local_midnight + timedelta(minutes=17)).astimezone(UTC)
    assert anchor.date() < local_day

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)

    monkeypatch.setattr(store, "datetime", Frozen)
    monkeypatch.setattr(event_creation, "datetime", Frozen)
    monkeypatch.setattr(event_draft, "datetime", Frozen)
    monkeypatch.setattr(event_choices, "datetime", Frozen)
    monkeypatch.setattr(conversation_guard, "datetime", Frozen)
    await ready(db_sessionmaker)
    request = turn("create me a event at 4pm tmrw for a meeting with kelly")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", intent=None),
            tool(
                "find_busy_times",
                subject="self",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
            ),
            tool(
                "prepare_calendar_event",
                title="meeting with kelly",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                time="16:00",
                time_source="4pm",
            ),
        ),
    )
    assert result["trace"][1]["reason"] == "calendar_preparation_required"
    assert result["kind"] == "calendar_event", result["trace"]
    action = result["calendar_action"]
    assert action["state"] == "proposed"
    event = action["preview"]["event"]
    assert event["summary"] == "meeting with kelly"
    local_start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    )
    assert local_start == local_midnight + timedelta(days=1, hours=16)
    assert event["attendees"] == []
    async with db_sessionmaker() as db:
        saved = await db.get(AssistantAction, result["calendar_action_id"])
        assert saved.payload["send_updates"] == "none"
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not any(call.url.path.endswith("/events") for call in configured[0])
