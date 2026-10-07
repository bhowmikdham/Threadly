"""Integrated chat goals and email-card approvals remain separate."""
# ruff: noqa: F811

from sqlalchemy import select

from app.calendar import permissions
from app.config import get_settings
from app.conversation import service, store
from app.db.models import ActionApproval, ActionJob, AssistantAction, Conversation, ConversationGoal
from app.schemas.conversation import CalendarApprovalSetting
from tests.test_calendar_creation import turn
from tests.test_chat_context_continuity import next_turn
from tests.test_conversation import Model, tool
from tests.test_meeting_email_event import configured, draft, fields, preview, setup  # noqa: F401


async def test_email_button_preserves_chat_goals_and_never_inherits_chat_always(
    configured, db_client, auth_headers, db_sessionmaker, monkeypatch
):
    monkeypatch.setenv("CONVERSATION_ENABLED", "true")
    get_settings.cache_clear()
    request = turn("Draft an email to Alex")
    first = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(tool("prepare_email_draft", recipient="Alex")),
    )
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    pending, _ = await next_turn(
        db_sessionmaker,
        first,
        "Create an event called Focus",
        tool("prepare_calendar_event", title="Focus"),
    )
    assert pending["kind"] == "clarification"
    async with db_sessionmaker() as db:
        before = store.decode(await db.get(Conversation, request.conversation_id))
        retained = (
            await db.scalars(
                select(ConversationGoal).where(
                    ConversationGoal.conversation_id == request.conversation_id
                )
            )
        ).all()
        goal_hashes = {g.goal_id: g.payload_hash for g in retained}
        assert {g.kind for g in retained} == {"calendar_event", "email_draft"}

    meeting = preview(db_client, auth_headers, fields(draft(db_client, auth_headers)))
    assert meeting["authorization"] == "separate_exact_event_approval"
    async with db_sessionmaker() as db:
        assert store.decode(await db.get(Conversation, request.conversation_id)) == before
        retained = (
            await db.scalars(
                select(ConversationGoal).where(
                    ConversationGoal.conversation_id == request.conversation_id
                )
            )
        ).all()
        assert {g.goal_id: g.payload_hash for g in retained} == goal_hashes

    resumed, _ = await next_turn(
        db_sessionmaker,
        pending,
        "Tomorrow at 14:00",
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            date={"kind": "relative", "offset_days": 1},
            date_source="Tomorrow",
            time="14:00",
            time_source="14:00",
        ),
    )
    assert resumed["kind"] == "calendar_event", resumed
    assert resumed["calendar_action"]["authorization"] == "chat_permission"
    assert resumed["calendar_action_id"] != meeting["action_id"]
    async with db_sessionmaker() as db:
        meeting_action = await db.get(AssistantAction, meeting["action_id"])
        assert meeting_action.state == "proposed"
        assert await db.get(ActionJob, meeting_action.id) is None
        assert (
            await db.scalar(
                select(ActionApproval.id).where(ActionApproval.action_id == meeting_action.id)
            )
            is None
        )
        chat_action = await db.get(AssistantAction, resumed["calendar_action_id"])
        assert chat_action.state == "approved"
        assert await db.get(ActionJob, chat_action.id) is not None
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["arguments"]["title"] == "Focus"
        assert (
            await db.scalar(
                select(ConversationGoal.goal_id).where(
                    ConversationGoal.conversation_id == request.conversation_id,
                    ConversationGoal.kind == "email_draft",
                    ConversationGoal.status == "retained",
                )
            )
            is not None
        )
    assert not any(c.url.path.endswith("/events") for c in configured[1])
