"""Replay the production model's truncated intent without relaxing source authority."""
# ruff: noqa: F811

import copy

from app.conversation import service, store
from app.db.models import AssistantAction, Conversation
from tests.test_calendar_creation import ARGS, configured, ready, run, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool


class ObservedModel(Model):
    async def decide(self, system, messages, tools):
        self.messages = copy.deepcopy(messages)
        return await super().decide(system, messages, tools)


async def test_truncated_polite_intent_repairs_before_missing_day(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    text = "Hi, could you create an event called Threadly validation at 4pm in Validation room?"
    args = {
        "title": "Threadly validation",
        "time": "16:00",
        "time_source": "4pm",
        "location": "Validation room",
    }
    shortened = "create an event called Threadly validation at 4pm in Validation room"
    model = ObservedModel(
        tool("prepare_calendar_event", **args, intent={"operation": "create", "source": shortened}),
        tool("prepare_calendar_event", **args, intent={"operation": "create", "source": text}),
    )
    request = turn(text)
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert result["kind"] == "clarification" and result["text"] == "What day should I use?"
    assert len(model.contexts) == 2
    feedback = model.messages[-1]["content"][0]["toolResult"]
    assert feedback["status"] == "error"
    assert feedback["content"][0]["json"]["error"] == "calendar_intent_source_mismatch"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
    saved = state["calendar_event_request"]["arguments"]
    assert saved["title"] == args["title"] and saved["location"] == args["location"]
    assert saved["time"] == "16:00" and saved["date"] is None
    assert configured[0] == []


async def test_bad_revision_source_cannot_retire_preview(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("Create Focus at 2pm tmrw")
    first = await run(db_sessionmaker, request, ARGS)
    action_id = first["calendar_action"]["action_id"]
    text = "Please change the time to 5pm."
    correction = turn(
        text, conversation_id=request.conversation_id, expected_version=first["version"]
    )
    changes = [{"field": "time", "operation": "replace", "source": "5pm", "value": "17:00"}]

    class CheckUnchanged(ObservedModel):
        async def decide(self, system, messages, tools):
            async with db_sessionmaker() as db:
                assert (await db.get(AssistantAction, action_id)).state == "proposed"
                saved = store.decode(await db.get(Conversation, request.conversation_id))
                assert saved["calendar_event_request"]["arguments"]["time"] == "14:00"
            return await super().decide(system, messages, tools)

    model = CheckUnchanged(
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            changes=changes,
            intent={"operation": "revise", "source": "change the time to 5pm"},
        ),
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            changes=changes,
            intent={"operation": "revise", "source": text},
        ),
    )
    result = await service.turn(1, correction, factory=db_sessionmaker, model=model)
    assert len(model.contexts) == 2 and result["kind"] == "calendar_event"
    assert result["calendar_action"]["action_id"] != action_id
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, action_id)).state == "superseded"


async def test_unrepaired_intent_is_bounded_and_creates_nothing(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    text = "Hi, could you create an event called Threadly validation at 4pm?"
    request = turn(text)
    model = ObservedModel(
        *[
            tool(
                "prepare_calendar_event",
                title="Threadly validation",
                time="16:00",
                time_source="4pm",
                intent={
                    "operation": "create",
                    "source": "create " + " " * n + "Threadly validation",
                },
            )
            for n in range(8)
        ]
    )
    result = await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert len(model.contexts) == 8
    assert result["kind"] == "message"
    assert result["error_code"] == "calendar_event_not_prepared"
    assert "Quote the complete" not in result["text"]
    assert "calendar_action" not in result
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
    assert "calendar_event_request" not in state
    assert configured[0] == []
