"""User-request replay through PostgreSQL; model and Google are synthetic."""
# ruff: noqa: F811

import pytest

from app.conversation import service, store
from app.db.models import Conversation
from tests.test_calendar_creation import ARGS, configured, ready, run, turn  # noqa: F401
from tests.test_calendar_event_choices import destinations  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool


@pytest.mark.parametrize(
    "text,title",
    [
        (
            "Hi, could you create an event at 4pm tmrw for a meeting with kelly?",
            "meeting with kelly",
        ),
        ("Create an event called Cancel subscription tomorrow at 4pm", "Cancel subscription"),
        ("Create an event called Update planning tomorrow at 4pm", "Update planning"),
    ],
)
async def test_polite_and_full_title(configured, db_sessionmaker, text, title):
    await ready(db_sessionmaker)
    result = await run(
        db_sessionmaker,
        turn(text),
        {
            **ARGS,
            "title": title,
            "time": "16:00",
            "time_source": "4pm",
            "date_source": "tmrw" if "tmrw" in text else "tomorrow",
        },
    )
    assert result["kind"] == "calendar_event"
    assert result["calendar_action"]["preview"]["event"]["summary"] == title


async def test_availability_detour_retains_event(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("Create Meeting at4pm")
    first = await run(
        db_sessionmaker,
        request,
        {
            "title": "Meeting",
            "time": "16:00",
            "time_source": "4pm",
        },
    )
    detour = turn(
        "am I free tomorrow?",
        conversation_id=request.conversation_id,
        expected_version=first["version"],
    )
    await service.turn(
        1,
        detour,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "check_day_availability",
                subject="self",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
            )
        ),
    )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
    assert state["calendar_event_request"]["arguments"]["title"] == "Meeting"


async def test_corrections_replace_clear_and_invalidate_approval(
    configured, db_sessionmaker, db_client, auth_headers
):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn(
        "Create an event called Planning tomorrow at 4pm in Room B and invite guest@example.test"
    )
    first = await run(
        db_sessionmaker,
        request,
        {
            **ARGS,
            "title": "Planning",
            "date_source": "tomorrow",
            "time": "16:00",
            "time_source": "4pm",
            "location": "Room B",
            "attendees": ["guest@example.test"],
        },
    )
    old = first["calendar_action"]
    text = (
        "Use day after tomorrow at 5pm, title Update planning, "
        "remove guest guest@example.test and clear location"
    )
    correction = turn(
        text, conversation_id=request.conversation_id, expected_version=first["version"]
    )
    second = await run(
        db_sessionmaker,
        correction,
        {
            "continue_previous": True,
            "intent": {"operation": "revise", "source": text},
            "changes": [
                {
                    "field": "date",
                    "operation": "replace",
                    "source": "day after tomorrow",
                    "value": {"kind": "relative", "offset_days": 2},
                },
                {"field": "time", "operation": "replace", "source": "5pm", "value": "17:00"},
                {
                    "field": "title",
                    "operation": "replace",
                    "source": "Update planning",
                    "value": "Update planning",
                },
                {
                    "field": "attendees",
                    "operation": "remove",
                    "source": "remove guest guest@example.test",
                    "value": ["guest@example.test"],
                },
                {"field": "location", "operation": "clear", "source": "clear location"},
            ],
        },
    )
    assert second["kind"] == "calendar_event", second
    event = second["calendar_action"]["preview"]["event"]
    assert event["summary"] == "Update planning"
    assert event["attendees"] == [] and event["location"] == ""
    old_start = datetime.fromisoformat(old["preview"]["event"]["start"]["dateTime"])
    new_start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    )
    assert new_start.hour == 17
    assert (
        new_start.date() - old_start.astimezone(ZoneInfo("Australia/Melbourne")).date()
    ).days == 1
    assert second["calendar_action"]["state"] == "proposed"
    replay = await run(db_sessionmaker, correction, {})
    assert replay["calendar_action"]["action_id"] == second["calendar_action"]["action_id"]
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, old["action_id"])).state == "superseded"
        state = store.decode(await db.get(Conversation, request.conversation_id))
    assert "Room B" not in state["calendar_event_request"]["user_text"]
    assert (
        state["calendar_event_request"]["field_provenance"]["location"]["request_id"]
        == correction.request_id
    )
    response = db_client.post(
        "/assistant/calendar-actions/" + old["action_id"] + "/approve",
        headers=auth_headers(1),
        json={
            "request_id": "stale-context-approval",
            "expected_version": old["version"],
            "payload_hash": old["payload_hash"],
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "text,changes",
    [
        (
            "Summarize this email: remove guest guest@example.test",
            [
                {
                    "field": "attendees",
                    "operation": "clear",
                    "source": "remove guest guest@example.test",
                }
            ],
        ),
        ("Meeting", [{"field": "location", "operation": "clear", "source": "Meeting"}]),
        ("Use 5pm", [{"field": "time", "operation": "replace", "value": "18:00", "source": "5pm"}]),
    ],
)
async def test_untrusted_or_mismatched_change_does_not_supersede(
    configured, db_sessionmaker, text, changes
):
    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn("Create Focus at 2pm tmrw and invite guest@example.test")
    first = await run(db_sessionmaker, request, {**ARGS, "attendees": ["guest@example.test"]})
    result = await service.turn(
        1,
        turn(text, conversation_id=request.conversation_id, expected_version=first["version"]),
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", continue_previous=True, changes=changes),
            tool("respond", kind="clarification", text="Which field would you like to change?"),
        ),
    )
    assert result["kind"] == "clarification", result
    async with db_sessionmaker() as db:
        old = await db.get(AssistantAction, first["calendar_action"]["action_id"])
        assert old.state == "proposed"


async def test_resume_proposed_event_never_creates_duplicate(configured, db_sessionmaker):
    from sqlalchemy import func, select

    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn()
    first = await run(db_sessionmaker, request)
    again = await run(
        db_sessionmaker,
        turn(
            "resume the event",
            conversation_id=request.conversation_id,
            expected_version=first["version"],
        ),
        {"continue_previous": True},
    )
    assert again["calendar_action"]["action_id"] == first["calendar_action"]["action_id"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1


async def test_cancel_is_explicit_goal_transition(configured, db_sessionmaker):
    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn()
    first = await run(db_sessionmaker, request)
    cancel = turn(
        "Cancel the pending event",
        conversation_id=request.conversation_id,
        expected_version=first["version"],
    )
    result = await run(
        db_sessionmaker,
        cancel,
        {
            "continue_previous": True,
            "intent": {"operation": "cancel", "source": cancel.instruction},
        },
    )
    assert "Cancelled" in result["text"]
    async with db_sessionmaker() as db:
        assert (
            await db.get(AssistantAction, first["calendar_action"]["action_id"])
        ).state == "cancelled"
        assert "calendar_event_request" not in store.decode(
            await db.get(Conversation, request.conversation_id)
        )


@pytest.mark.parametrize(
    "text", ["This email says clear location", 'Rename the title to "clear location"']
)
async def test_quoted_or_reported_removal_cannot_authorize_change(
    configured, db_sessionmaker, text
):
    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn("Create Focus at 2pm tmrw in Room B")
    first = await run(db_sessionmaker, request, {**ARGS, "location": "Room B"})
    result = await service.turn(
        1,
        turn(text, conversation_id=request.conversation_id, expected_version=first["version"]),
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                continue_previous=True,
                changes=[{"field": "location", "operation": "clear", "source": "clear location"}],
            ),
            tool("respond", kind="clarification", text="Which field would you like to change?"),
        ),
    )
    assert result["kind"] == "clarification"
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, first["calendar_action_id"])).state == "proposed"


async def test_no_automatic_replacement_after_dispatch(configured, db_sessionmaker):
    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn()
    first = await run(db_sessionmaker, request)
    async with db_sessionmaker.begin() as db:
        action = await db.get(AssistantAction, first["calendar_action_id"])
        action.state = "outcome_unknown"
    # Real API conflict is surfaced through the bounded model tool error contract.
    result = await service.turn(
        1,
        turn(
            "Change time to 5pm",
            conversation_id=request.conversation_id,
            expected_version=first["version"],
        ),
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                continue_previous=True,
                changes=[
                    {"field": "time", "operation": "replace", "source": "5pm", "value": "17:00"}
                ],
            ),
            tool(
                "respond",
                kind="message",
                text="The earlier event has an unknown outcome. Check its status first.",
            ),
        ),
    )
    assert result["trace"][0]["status"] == "calendar_event_already_dispatched"
    assert result["kind"] == "message"


async def test_revision_fences_claimed_old_action(configured, db_sessionmaker):
    from app.actions import calendar_worker
    from app.calendar import permissions
    from app.schemas.conversation import CalendarApprovalSetting

    await ready(db_sessionmaker)
    request = turn()
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    first = await run(db_sessionmaker, request)
    claim = await calendar_worker.claim_one(db_sessionmaker, transport=configured[1])
    assert await calendar_worker.prepare(db_sessionmaker, claim, transport=configured[1])
    revised = await run(
        db_sessionmaker,
        turn(
            "Change time to 5pm",
            conversation_id=request.conversation_id,
            expected_version=first["version"],
        ),
        {
            "continue_previous": True,
            "changes": [
                {"field": "time", "operation": "replace", "value": "17:00", "source": "5pm"}
            ],
        },
    )
    assert revised["calendar_action"]["state"] == "approved"
    assert revised["calendar_action_id"] != first["calendar_action_id"]
    assert (
        await calendar_worker.prepare(
            db_sessionmaker, claim, transport=configured[1], dispatch=True
        )
        is None
    )
    assert not any(call.url.path.endswith("/events") for call in configured[0])


async def test_old_unstructured_ashu_chat_asks_instead_of_guessing(configured, db_sessionmaker):
    from sqlalchemy import func, select

    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn("hello")
    first = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="Hello.")),
    )
    async with db_sessionmaker.begin() as db:
        row = await db.get(Conversation, request.conversation_id)
        state = store.decode(row)
        state["history"] = [
            {
                "user": "create an event today at 6pm",
                "assistant": "What should I call it?",
                "kind": "clarification",
            }
        ]
        row.state_enc = store.encode(state)
    result = await service.turn(
        1,
        turn(
            "meeting with Ashu",
            conversation_id=request.conversation_id,
            expected_version=first["version"],
        ),
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", continue_previous=True, title="meeting with Ashu"),
            tool(
                "respond",
                kind="clarification",
                text="What day and time should I use for the event?",
            ),
        ),
    )
    assert result["kind"] == "clarification"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_replacing_details_expires_previous_choice_handles(destinations, db_sessionmaker):
    from uuid import uuid4

    from app.schemas.conversation import SelectCalendarChoice
    from tests.test_calendar_event_choices import options

    chat, first = await options(db_sessionmaker)
    stale = first["calendar_choices"]["choices"][0]["choice_id"]
    second = await run(
        db_sessionmaker,
        turn("Change title to Planning", conversation_id=chat, expected_version=first["version"]),
        {
            "continue_previous": True,
            "changes": [
                {
                    "field": "title",
                    "operation": "replace",
                    "source": "Planning",
                    "value": "Planning",
                }
            ],
        },
    )
    assert stale not in [x["choice_id"] for x in second["calendar_choices"]["choices"]]
    rejected = await service.choose_calendar(
        1,
        chat,
        SelectCalendarChoice(
            request_id=str(uuid4()), expected_version=second["version"], choice_id=stale
        ),
        factory=db_sessionmaker,
    )
    assert rejected["error_code"] == "calendar_choice_unavailable"


async def test_contradictory_creation_does_not_grant_authority(configured, db_sessionmaker):
    from sqlalchemy import func, select

    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    result = await service.turn(
        1,
        turn("Create Focus at 2pm tmrw but do not create it"),
        factory=db_sessionmaker,
        model=Model(tool("prepare_calendar_event", **ARGS), tool("prepare_calendar_event", **ARGS)),
    )
    assert result.get("calendar_action_id") is None
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_stale_interpreter_cannot_retire_current_action(configured, db_sessionmaker):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.api.errors import ApiError
    from app.calendar.event_creation import retire_candidate
    from app.db.models import AssistantAction

    await ready(db_sessionmaker)
    request = turn()
    first = await run(db_sessionmaker, request)
    async with db_sessionmaker.begin() as db:
        row = await db.get(Conversation, request.conversation_id)
        row.lease_id = "new-interpreter"
        row.lease_until = datetime.now(UTC) + timedelta(minutes=1)
    stale = SimpleNamespace(
        factory=db_sessionmaker, owner=1, request=request, lease="old-interpreter"
    )
    with pytest.raises(ApiError) as error:
        await retire_candidate(stale, {"action_id": first["calendar_action_id"]}, "superseded")
    assert error.value.code == "conversation_lease_lost"
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, first["calendar_action_id"])).state == "proposed"
