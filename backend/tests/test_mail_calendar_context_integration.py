"""Combined release: retained mail context and Calendar continuation remain independent."""

import json

import pytest
from sqlalchemy import func, select

from app.calendar import permissions
from app.conversation import mail_context, service, store
from app.db.models import ActionJob, AssistantAction
from app.schemas.conversation import CalendarApprovalSetting
from tests.conftest import needs_pg
from tests.test_calendar_creation import ARGS, configured, ready, setup, turn  # noqa: F401
from tests.test_conversation import Model, tool

pytestmark = needs_pg


@pytest.mark.parametrize(
    "mode,expected_state,jobs", [("ask", "proposed", 0), ("always", "approved", 1)]
)
async def test_calendar_continuation_preserves_retained_mail_and_permission(
    configured, db_sessionmaker, mode, expected_state, jobs  # noqa: F811
):
    await ready(db_sessionmaker)
    request = turn("could you craete an event at 2pm tmrw?")
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode=mode, expected_version=0),
        factory=db_sessionmaker,
    )
    # A previously read source is retained as a handle, without carrying mail text
    # into the later Calendar request or conferring any action authority.
    async with db_sessionmaker.begin() as session:
        row = await store.owned(session, 1, request.conversation_id, lock=True)
        state = store.decode(row)
        state["refs"]["mail-1"] = {"thread_id": "abc123", "message_id": "def456"}
        handle = mail_context.retain(state, "mail-1", "thread")
        mail_context.reset_search(state)
        row.state_enc = store.encode(state)

    first_model = Model(tool("prepare_calendar_event", **{**ARGS, "title": ""}))
    first = await service.turn(1, request, factory=db_sessionmaker, model=first_model)
    assert first["text"] == "What should I call the event?"
    assert first_model.contexts[0]["remembered_email_sources"][0]["reference"] == handle
    second_request = turn(
        "Focus", conversation_id=request.conversation_id, expected_version=first["version"]
    )
    second_model = Model(
        tool("prepare_calendar_event", continue_previous=True, title="Focus")
    )
    result = await service.turn(
        1, second_request, factory=db_sessionmaker, model=second_model
    )
    context = second_model.contexts[0]
    assert context["pending_calendar_event"]["arguments"]["time"] == "14:00"
    assert context["remembered_email_sources"][0]["reference"] == handle
    action = result["calendar_action"]
    assert action["state"] == expected_state
    assert action["preview"]["event"]["summary"] == "Focus"
    assert result["context_references"] == []  # This event did not use the mail source.
    assert not any(call.method == "POST" for call in configured[0])

    async with db_sessionmaker() as session:
        row = await store.owned(session, 1, request.conversation_id)
        state = store.decode(row)
        assert row.calendar_approval_mode == mode
        assert state["refs"][handle]["thread_id"] == "abc123"
        assert state["context_order"] == [handle]
        assert state["history"][-1]["calendar_action_id"] == action["action_id"]
        assert state["history"][-1]["context_references"] == []
        assert "calendar_action" not in state["receipts"][-1]["response"]
        assert "dateTime" not in json.dumps(state["receipts"][-1])
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == jobs

    # Idempotent replay rehydrates the Calendar result without losing mail handles
    # or promoting an Ask preview into an approved action.
    replay = await service.turn(1, second_request, factory=db_sessionmaker, model=Model())
    assert replay["calendar_action"]["action_id"] == action["action_id"]
    assert replay["calendar_action"]["state"] == expected_state
