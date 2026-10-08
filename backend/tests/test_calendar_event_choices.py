"""Full creation clarifications and UI selection; only synthetic Google/isolated DB."""
# ruff: noqa: F811

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import client, event_choices, permissions
from app.conversation import service, store
from app.db.models import ActionJob, AssistantAction, CalendarPreference, Conversation
from app.schemas.conversation import CalendarApprovalSetting, SelectCalendarChoice
from tests.test_calendar_creation import configured, ready, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool


@pytest.fixture
def destinations(configured, monkeypatch):
    rows = [
        {"id": "private-room-id", "summary": "Personal meeting room", "accessRole": "owner"},
        {"id": "family-private-id", "summary": "Family", "accessRole": "writer"},
        {"id": "work@example.test", "summary": "Work inbox", "accessRole": "owner"},
        {"id": "holiday-private-id", "summary": "Holidays in India", "accessRole": "reader"},
    ]
    previous = client._request

    # Exercise the real Google list adapter and its ACL normalization.
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": rows}))

    async def routed(*args, **kwargs):
        if "calendarList" in str(args):
            kwargs["transport"] = transport
            from tests.test_calendar_creation import BASE_REQUEST

            return await BASE_REQUEST(*args, **kwargs)
        return await previous(*args, **kwargs)

    monkeypatch.setattr(client, "_request", routed)
    return rows


async def prepare_chat(factory, mode="ask"):
    await ready(factory)
    async with factory.begin() as db:
        pref = await db.get(CalendarPreference, 1)
        pref.preferences = {
            **pref.preferences,
            "calendar_ids": [
                "private-room-id",
                "family-private-id",
                "work@example.test",
                "holiday-private-id",
            ],
        }
    first = turn("Delete my old event")
    await permissions.set_mode(
        1,
        first.conversation_id,
        CalendarApprovalSetting(mode=mode, expected_version=0),
        factory=factory,
    )
    result = await service.turn(
        1,
        first,
        factory=factory,
        model=Model(
            tool("respond", kind="message", text="Event deletion isn't available through me yet.")
        ),
    )
    return first.conversation_id, result["version"]


async def advance(factory, chat, version, instruction, arguments, wrong=None):
    # Repair routing after the model has chosen Calendar; the backend no longer
    # classifies the user's free text before any model-selected operation.
    decisions = ([tool("prepare_calendar_event", intent=None), wrong] if wrong else [])
    decisions += [tool("prepare_calendar_event", **arguments)]
    return await service.turn(
        1,
        turn(instruction, conversation_id=chat, expected_version=version),
        factory=factory,
        model=Model(*decisions),
    )


async def options(factory, mode="ask"):
    chat, version = await prepare_chat(factory, mode)
    result = await advance(
        factory,
        chat,
        version,
        "could you create Meeting at4pm",
        {"title": "Meeting", "time": "16:00", "time_source": "4pm"},
        tool("find_busy_times", date_phrase="tomorrow"),
    )
    assert result["text"] == "What day should I use?"
    result = await advance(
        factory,
        chat,
        result["version"],
        "tomorrow",
        {
            "continue_previous": True,
            "date": {"kind": "relative", "offset_days": 1},
            "date_source": "tomorrow",
        },
        tool("list_calendars"),
    )
    assert [choice["label"] for choice in result["calendar_choices"]["choices"]] == [
        "Personal meeting room",
        "Family",
        "Work inbox",
    ]
    assert all(choice["access"] == "editable" for choice in result["calendar_choices"]["choices"])
    assert "private-id" not in json.dumps(result)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    return chat, result


@pytest.mark.parametrize("mode", ["ask", "always"])
@pytest.mark.parametrize("selection", ["work@example.test", "3rd one"])
async def test_full_creation_followups_keep_fields_and_ground_calendar_selection(
    destinations, db_sessionmaker, mode, selection
):
    chat, result = await options(db_sessionmaker, mode)
    # Calendar discovery is a supported detour, not a new goal that erases creation.
    result = await service.turn(
        1,
        turn("show calendars", conversation_id=chat, expected_version=result["version"]),
        factory=db_sessionmaker,
        model=Model(tool("list_calendars")),
    )
    # Provider reorders the list after the user has seen it. Ordinal still means
    # the third OFFERED choice, not the third row in a new provider response.
    destinations.reverse()
    result = await advance(
        db_sessionmaker,
        chat,
        result["version"],
        selection,
        {"continue_previous": True, "calendar_name": selection},
        tool("list_calendars"),
    )
    action = result["calendar_action"]
    assert action["state"] == ("proposed" if mode == "ask" else "approved")
    assert action["preview"]["calendar_name"] == "Work inbox"
    assert action["preview"]["event"]["summary"] == "Meeting"
    assert (
        datetime.fromisoformat(action["preview"]["event"]["start"]["dateTime"])
        .astimezone(ZoneInfo("Australia/Melbourne"))
        .hour
        == 16
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == (mode == "always")


@pytest.mark.parametrize("mode", ["ask", "always"])
async def test_click_bypasses_model_and_replays_one_owned_candidate(
    destinations, db_sessionmaker, monkeypatch, mode
):
    chat, result = await options(db_sessionmaker, mode)
    restored = await service.get(1, chat, factory=db_sessionmaker)
    assert restored["calendar_choices"] == result["calendar_choices"]
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=result["version"],
        choice_id=result["calendar_choices"]["choices"][2]["choice_id"],
    )

    async def no_model(*args, **kwargs):
        raise AssertionError("A click must not call the model")

    monkeypatch.setattr(service.engine, "run", no_model)
    first = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    second = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert first["calendar_action_id"] == second["calendar_action_id"]
    assert first["calendar_action"]["state"] == ("proposed" if mode == "ask" else "approved")
    assert (await service.get(1, chat, factory=db_sessionmaker))["calendar_choices"] is None
    changed = body.model_copy(
        update={"choice_id": result["calendar_choices"]["choices"][0]["choice_id"]}
    )
    with pytest.raises(ApiError) as error:
        await service.choose_calendar(1, chat, changed, factory=db_sessionmaker)
    assert error.value.code == "idempotency_conflict"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == (mode == "always")


async def test_choice_route_owner_version_and_forged_choice(
    destinations, db_sessionmaker, db_client, auth_headers
):
    chat, result = await options(db_sessionmaker)
    path = f"/assistant/conversations/{chat}/calendar-choice"
    body = {
        "request_id": str(uuid4()),
        "expected_version": result["version"],
        "choice_id": result["calendar_choices"]["choices"][0]["choice_id"],
    }
    assert db_client.post(path, json=body).status_code == 401
    assert db_client.post(path, json=body, headers=auth_headers(2)).status_code == 404
    assert (
        db_client.post(
            path, json={**body, "expected_version": 0}, headers=auth_headers(1)
        ).status_code
        == 409
    )
    response = db_client.post(
        path, json={**body, "choice_id": str(uuid4())}, headers=auth_headers(1)
    )
    assert (
        response.status_code == 200
        and response.json()["error_code"] == "calendar_choice_unavailable"
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


@pytest.mark.parametrize("change", ["acl", "preferences", "expiry"])
async def test_stale_choices_never_choose_a_replacement_or_create(
    destinations, db_sessionmaker, change
):
    chat, result = await options(db_sessionmaker, "always")
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=result["version"],
        choice_id=result["calendar_choices"]["choices"][2]["choice_id"],
    )
    if change == "acl":
        destinations[2]["accessRole"] = "reader"
    async with db_sessionmaker.begin() as db:
        if change == "preferences":
            pref = await db.get(CalendarPreference, 1)
            pref.version += 1
        if change == "expiry":
            row = await db.get(Conversation, chat)
            state = store.decode(row)
            state["calendar_event_request"]["expires_at"] = (
                datetime.now(UTC) - timedelta(seconds=1)
            ).isoformat()
            row.state_enc = store.encode(state)
    result = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert result["error_code"] == (
        "calendar_choice_unavailable" if change == "expiry" else "calendar_choices_changed"
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_duplicate_names_need_choice_and_click_before_date_keeps_fields(
    destinations, db_sessionmaker
):
    destinations[0]["summary"] = destinations[1]["summary"] = "Team"
    chat, version = await prepare_chat(db_sessionmaker)
    first = await advance(
        db_sessionmaker,
        chat,
        version,
        "create Focus at 4 pm",
        {"title": "Focus", "time": "16:00", "time_source": "4 pm"},
    )
    shown = await service.turn(
        1,
        turn("list calendars", conversation_id=chat, expected_version=first["version"]),
        factory=db_sessionmaker,
        model=Model(tool("list_calendars")),
    )
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=shown["version"],
        choice_id=shown["calendar_choices"]["choices"][1]["choice_id"],
    )
    chosen = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert chosen["text"] == "What day should I use?"
    result = await advance(
        db_sessionmaker,
        chat,
        chosen["version"],
        "tmrw",
        {
            "continue_previous": True,
            "date": {"kind": "relative", "offset_days": 1},
            "date_source": "tmrw",
        },
    )
    async with db_sessionmaker() as db:
        action = await db.get(AssistantAction, result["calendar_action_id"])
        assert action.payload["calendar_id"] == "family-private-id"
    assert result["calendar_action"]["preview"]["event"]["summary"] == "Focus"


def test_newly_supplied_relative_date_uses_its_followup_anchor():
    from app.calendar import event_draft
    from app.schemas.conversation import PrepareCalendarEvent

    old = datetime(2026, 10, 5, 12, tzinfo=UTC)
    new = datetime(2026, 10, 5, 14, tzinfo=UTC)  # after Melbourne midnight
    runtime = SimpleNamespace(
        state={
            "calendar_event_request": {
                "arguments": {"title": "Focus", "time": "16:00", "time_source": "4 pm"},
                "user_text": "create Focus at 4 pm",
                "anchor": old.isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        },
        request=SimpleNamespace(instruction="tomorrow", request_id=str(uuid4())),
        calendar_anchor=new,
    )
    # Inspect the proposed state before preference-dependent date validation and
    # persistence. A rejected interpretation must not publish this state early.
    _, saved, _ = event_draft.merge(
        runtime,
        PrepareCalendarEvent(
            continue_previous=True,
            intent={"operation": "resume", "source": "tomorrow"},
            date={"kind": "relative", "offset_days": 1},
            date_source="tomorrow",
        ),
        runtime.state["calendar_event_request"],
    )
    assert saved["anchor"] == new.isoformat()
    assert runtime.state["calendar_event_request"]["anchor"] == old.isoformat()


async def test_separate_title_day_and_time_answers_only_ask_for_missing_fields(
    destinations, db_sessionmaker
):
    chat, version = await prepare_chat(db_sessionmaker)
    first = await advance(db_sessionmaker, chat, version, "create me an event on the calendar", {})
    assert first["text"] == "What should I call the event?"
    title = await advance(
        db_sessionmaker,
        chat,
        first["version"],
        "Meeting",
        {"continue_previous": True, "title": "Meeting"},
    )
    assert title["text"] == "What day should I use?"
    day = await advance(
        db_sessionmaker,
        chat,
        title["version"],
        "tmrw",
        {
            "continue_previous": True,
            "date": {"kind": "relative", "offset_days": 1},
            "date_source": "tmrw",
        },
        tool("read_calendar", period="tomorrow"),
    )
    assert day["text"] == "What time should it start? Please include AM or PM."
    timed = await advance(
        db_sessionmaker,
        chat,
        day["version"],
        "4 pm",
        {"continue_previous": True, "time": "16:00", "time_source": "4 pm"},
    )
    assert len(timed["calendar_choices"]["choices"]) == 3
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, chat))
        context = event_choices.model_context(state)
        assert context["arguments"]["title"] == "Meeting"
        assert context["arguments"]["date_source"] == "tmrw"
        assert context["arguments"]["time"] == "16:00"
        assert "private-room-id" not in json.dumps(context)
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_recover_clicked_calendar_with_remaining_fields(
    destinations, db_sessionmaker, monkeypatch
):
    from app.conversation import recovery
    from app.schemas.conversation import RecoverConversation

    chat, version = await prepare_chat(db_sessionmaker)
    first = await advance(
        db_sessionmaker,
        chat,
        version,
        "create Focus at 4 pm",
        {"title": "Focus", "time": "16:00", "time_source": "4 pm"},
    )
    shown = await service.turn(
        1,
        turn("show calendars", conversation_id=chat, expected_version=first["version"]),
        factory=db_sessionmaker,
        model=Model(tool("list_calendars")),
    )
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=shown["version"],
        choice_id=shown["calendar_choices"]["choices"][1]["choice_id"],
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("lost final response")

    with monkeypatch.context() as m:
        m.setattr(store, "complete", fail)
        with pytest.raises(RuntimeError):
            await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    recovered = await recovery.recover(
        1,
        chat,
        RecoverConversation(
            pending_request_id=body.request_id,
            expected_version=body.expected_version,
            operation="recover",
        ),
        factory=db_sessionmaker,
    )
    assert recovered["recovered_request_id"] == body.request_id
    assert recovered["text"] == "What day should I use?"
    assert recovered["calendar_choices"]["choices"] == shown["calendar_choices"]["choices"]
    result = await advance(
        db_sessionmaker,
        chat,
        recovered["version"],
        "tomorrow",
        {
            "continue_previous": True,
            "date": {"kind": "relative", "offset_days": 1},
            "date_source": "tomorrow",
        },
    )
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, result["calendar_action_id"])).payload[
            "calendar_id"
        ] == "family-private-id"
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_overlapping_clicks_use_one_turn_and_one_action(
    destinations, db_sessionmaker, monkeypatch
):
    chat, result = await options(db_sessionmaker)
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=result["version"],
        choice_id=result["calendar_choices"]["choices"][0]["choice_id"],
    )
    entered, release = asyncio.Event(), asyncio.Event()
    previous = client._request

    async def blocked(*args, **kwargs):
        if "calendarList" in str(args):
            entered.set()
            await release.wait()
        return await previous(*args, **kwargs)

    monkeypatch.setattr(client, "_request", blocked)
    first = asyncio.create_task(service.choose_calendar(1, chat, body, factory=db_sessionmaker))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        with pytest.raises(ApiError) as error:
            await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
        assert error.value.code == "conversation_busy"
    finally:
        release.set()
    result = await first
    replay = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert replay["calendar_action_id"] == result["calendar_action_id"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1


async def test_other_chat_choice_and_cancelled_creation_are_not_authority(
    destinations, db_sessionmaker
):
    chat, result = await options(db_sessionmaker)
    another = await advance(
        db_sessionmaker,
        str(uuid4()),
        0,
        "Create an event called Meeting at 4 pm tomorrow",
        {
            "title": "Meeting",
            "time": "16:00",
            "time_source": "4 pm",
            "date": {"kind": "relative", "offset_days": 1},
            "date_source": "tomorrow",
        },
    )
    body = SelectCalendarChoice(
        request_id=str(uuid4()),
        expected_version=another["version"],
        choice_id=result["calendar_choices"]["choices"][0]["choice_id"],
    )
    rejected = await service.choose_calendar(
        1, another["conversation_id"], body, factory=db_sessionmaker
    )
    assert rejected["error_code"] == "calendar_choice_unavailable"
    cancelled = await service.turn(
        1,
        turn("cancel", conversation_id=chat, expected_version=result["version"]),
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="Okay, I won't create it.")),
    )
    assert (await service.get(1, chat, factory=db_sessionmaker))["calendar_choices"] is None
    stale = body.model_copy(
        update={"request_id": str(uuid4()), "expected_version": cancelled["version"]}
    )
    assert (await service.choose_calendar(1, chat, stale, factory=db_sessionmaker))[
        "error_code"
    ] == "calendar_choice_unavailable"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_general_calendar_list_distinguishes_acl_from_busy_access(destinations):
    from app.calendar.conversation_tools import execute
    from app.schemas.calendar_tools import ListCalendars

    result = await execute(1, "list_calendars", ListCalendars(), "list calendars")
    assert "Personal meeting room (editable)" in result["text"]
    assert "Family (editable)" in result["text"]
    assert "Holidays in India (read-only)" in result["text"]


@pytest.mark.parametrize("text", ["Why the 3rd one?", 'This email says "3rd one"'])
async def test_mentions_and_quoted_ordinals_do_not_select_a_calendar(
    destinations, db_sessionmaker, text
):
    chat, result = await options(db_sessionmaker, "always")
    answer = await advance(
        db_sessionmaker,
        chat,
        result["version"],
        text,
        {"continue_previous": True, "calendar_name": "3rd one"},
    )
    assert answer["kind"] == "clarification" and "calendar_action_id" not in answer
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


@pytest.mark.parametrize("mode", ["ask", "always"])
async def test_choice_expiring_during_calendar_read_returns_unavailable_without_action(
    destinations, db_sessionmaker, monkeypatch, mode
):
    chat, result = await options(db_sessionmaker, mode)
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, chat))
        expires = datetime.fromisoformat(state["calendar_event_request"]["expires_at"])

    class Clock(datetime):
        value = expires - timedelta(seconds=1)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    monkeypatch.setattr(event_choices, "datetime", Clock)
    original = event_choices.service.list_calendars
    calls = []

    async def delayed_read(owner):
        calls.append(owner)
        calendars = await original(owner)
        Clock.value = expires
        return calendars

    monkeypatch.setattr(event_choices.service, "list_calendars", delayed_read)
    body = SelectCalendarChoice(
        request_id=str(uuid4()), expected_version=result["version"],
        choice_id=result["calendar_choices"]["choices"][2]["choice_id"],
    )
    reply = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert reply["error_code"] == "calendar_choice_unavailable"
    assert "calendar_action" not in reply
    replay = await service.choose_calendar(1, chat, body, factory=db_sessionmaker)
    assert replay == reply and calls == [1]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        row = await db.get(Conversation, chat)
        assert row.pending_request_id is None and row.calendar_approval_mode == mode
