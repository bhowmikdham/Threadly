"""The social detour must retain source references across real persisted turns."""

from copy import deepcopy

from app.assistant import source_data
from app.conversation import service, store
from app.db.models import Conversation
from tests.test_conversation import Model, configured, request, tool  # noqa: F401
from tests.test_on_demand_gmail import MID, TID, setup  # noqa: F401
from tests.test_shared_mail_context import mailbox, pinned  # noqa: F401


async def test_greeting_followup_then_tool_request_keep_the_same_pinned_source(
    mailbox, db_sessionmaker  # noqa: F811
):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        first = request(context_snapshot_id=selected.id).model_copy(
            update={"instruction": "Hey Threadly, how are you?"}
        )
        greeted = await service.turn(1, first, factory=db_sessionmaker, model=Model())
        assert "Threadly" not in greeted["text"] and "How are you" in greeted["text"]
        async with db_sessionmaker() as session:
            state = store.decode(await session.get(Conversation, first.conversation_id))
            original = deepcopy(state["refs"]["selected"])
        second = request().model_copy(
            update={
                "instruction": "I'm good, thanks",
                "conversation_id": first.conversation_id,
                "expected_version": 1,
            }
        )
        followup = await service.turn(1, second, factory=db_sessionmaker, model=Model())
        assert "Glad to hear" in followup["text"]
        async with db_sessionmaker() as session:
            state = store.decode(await session.get(Conversation, first.conversation_id))
            assert state["refs"]["selected"] == original
            assert len(state["history"]) == 2
        third = request().model_copy(
            update={
                "instruction": "Summarise that thread",
                "conversation_id": first.conversation_id,
                "expected_version": 2,
            }
        )
        model = Model(
            tool("read_email", reference="selected"),
            tool("prepare_workflow", intent="summarise", reference="selected"),
        )
        result = await service.turn(1, third, factory=db_sessionmaker, model=model)
        assert result["kind"] == "task"
        assert model.contexts[0]["selected_reference"] == "selected"
        assert model.contexts[0]["recent_dialogue"][0]["user"] == first.instruction
        async with db_sessionmaker() as session:
            state = store.decode(await session.get(Conversation, first.conversation_id))
            assert state["refs"]["selected"] == original
            assert original["thread_id"] == TID and original["message_id"] == MID
