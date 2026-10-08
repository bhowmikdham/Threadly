"""Cross-workflow storage/authority tests; model decisions and Gmail are scripted."""
# ruff: noqa: F811

from sqlalchemy import func, select

from app.assistant import source_data, worker
from app.conversation import recovery, service, store
from app.db.models import ActionJob, AssistantAction, AssistantTask, Conversation
from tests.test_calendar_creation import turn
from tests.test_chat_context_continuity import next_turn
from tests.test_conversation import Model, configured, tool  # noqa: F401
from tests.test_conversation_recovery import command, strand
from tests.test_on_demand_gmail import Model as Generator
from tests.test_on_demand_gmail import setup  # noqa: F401
from tests.test_shared_mail_context import mailbox, pinned  # noqa: F401


async def test_summary_reply_calendar_and_resume_loads_saved_work_without_reexecution(
    mailbox, db_sessionmaker
):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        request = turn("Summarise this thread", context_snapshot_id=selected.id)
        summary = await service.turn(
            1,
            request,
            factory=db_sessionmaker,
            model=Model(
                tool("read_email", reference="selected"),
                tool("prepare_workflow", intent="summarise", reference="selected"),
            ),
        )
    assert summary["kind"] == "task", summary
    assert await worker.run_once(db_sessionmaker, Generator())
    async with source_data.source_scope():
        reply, _ = await next_turn(
            db_sessionmaker,
            summary,
            "Draft a reply thanking them for confirming receipt",
            tool("read_email", reference="selected"),
            tool("prepare_workflow", intent="reply", reference="selected"),
        )
    assert reply["kind"] == "task", reply
    assert await worker.run_once(db_sessionmaker, Generator())
    calendar, _ = await next_turn(
        db_sessionmaker,
        reply,
        "Create Focus tomorrow",
        tool(
            "prepare_calendar_event",
            title="Focus",
            date={"kind": "relative", "offset_days": 1},
            date_source="tomorrow",
        ),
    )
    assert calendar["kind"] == "clarification", calendar
    text = "Go back to the earlier summary"
    resumed, _ = await next_turn(
        db_sessionmaker,
        calendar,
        text,
        tool("recall_conversation", query="Summarise"),
        tool("resume_conversation_task", turn_version=1, source=text),
        tool("respond", kind="message", text="Here is the earlier summary."),
    )
    assert resumed.get("task_id") == summary["task_id"], resumed
    assert resumed["task"]["state"] == "succeeded"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["arguments"]["title"] == "Focus"
        assert state["active_task_id"] == summary["task_id"]
        assert await db.scalar(select(func.count()).select_from(AssistantTask)) == 2
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not await worker.run_once(db_sessionmaker, Generator())


async def test_cancel_interrupted_detour_preserves_unrelated_calendar_goal(
    configured, db_sessionmaker
):
    request = turn("Create Focus tomorrow")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
            )
        ),
    )
    failed = turn(
        "Can you help with an email?",
        conversation_id=request.conversation_id,
        expected_version=result["version"],
    )
    await strand(db_sessionmaker, failed)
    recovered = await recovery.recover(
        1,
        request.conversation_id,
        command(failed),
        factory=db_sessionmaker,
    )
    assert recovered["error_code"] == "conversation_request_cancelled"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["arguments"]["title"] == "Focus"
