"""Cited historical data and independent goal identity through the real storage path."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.conversation import citations, goals, service, store
from app.db.models import ActionJob, AssistantAction, Conversation, ConversationGoal
from app.schemas.chat_context import FieldCitation
from tests.test_calendar_creation import configured, ready, setup, turn  # noqa: F401
from tests.test_chat_context_continuity import archived_chat, exchange, next_turn
from tests.test_conversation import Model, tool
from tests.test_email_draft_clarification import DRAFT


async def test_cited_calendar_fields_use_original_date_anchor_not_the_new_turn(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    anchor = datetime.now(UTC) - timedelta(days=1)
    text = "Design pairing is in three days at 9 PM AEST."
    runtime = await archived_chat(
        db_sessionmaker,
        [
            exchange(
                1,
                text,
                recorded_at=anchor.isoformat(),
                timezone="Australia/Melbourne",
            )
        ],
    )
    request = turn(
        "Please create the event we discussed.",
        conversation_id=runtime.request.conversation_id,
        expected_version=1,
    )
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="Design pairing",
                date={"kind": "relative", "offset_days": 3},
                date_source="in three days",
                time="21:00",
                time_source="9 PM AEST",
                citations=[
                    {"field": field, "turn_version": 1, "quote": quote}
                    for field, quote in (
                        ("title", "Design pairing"),
                        ("date", "in three days"),
                        ("time", "9 PM AEST"),
                    )
                ],
            )
        ),
    )
    assert result["kind"] == "calendar_event", result
    event = result["calendar_action"]["preview"]["event"]
    expected = anchor.astimezone(ZoneInfo("Etc/GMT-10")).date() + timedelta(days=3)
    assert datetime.fromisoformat(event["start"]["dateTime"]).date() == expected
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["field_provenance"]["date"]["turn_version"] == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_citations_reject_assistant_text_future_versions_and_other_owners(
    configured, db_sessionmaker
):
    runtime = await archived_chat(
        db_sessionmaker,
        [
            exchange(
                1, "An arbitrary detail: the blue lantern.", assistant="The invented red lantern."
            )
        ],
    )
    source = FieldCitation(field="purpose", turn_version=1, quote="the blue lantern")
    assert (await citations.resolve(runtime, [source]))["purpose"]["source"] == source.quote
    for invalid in (
        source.model_copy(update={"quote": "invented red lantern"}),
        source.model_copy(update={"turn_version": 2}),
    ):
        with pytest.raises(ValueError):
            await citations.resolve(runtime, [invalid])
    runtime.owner = 2
    with pytest.raises(ApiError) as failure:
        await citations.resolve(runtime, [source])
    assert failure.value.status == 404


async def test_old_relative_date_without_clock_is_not_reinterpreted_as_today(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    runtime = await archived_chat(db_sessionmaker, [exchange(1, "Focus tomorrow at 2pm")])
    result = await service.turn(
        1,
        turn(
            "Please create that event",
            conversation_id=runtime.request.conversation_id,
            expected_version=1,
        ),
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
                time="14:00",
                time_source="2pm",
                citations=[
                    {"field": field, "turn_version": 1, "quote": quote}
                    for field, quote in (
                        ("title", "Focus"),
                        ("date", "tomorrow"),
                        ("time", "2pm"),
                    )
                ],
            )
        ),
    )
    assert result["kind"] == "clarification", result
    assert "saved local date" in result["text"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_two_unfinished_email_goals_can_be_resumed_independently(configured, db_sessionmaker):
    request = turn("Draft an email to Alex")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(tool("prepare_email_draft", recipient="Alex")),
    )
    result, _ = await next_turn(
        db_sessionmaker,
        result,
        "Draft another email to Casey",
        tool("prepare_email_draft", recipient="Casey"),
    )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
    runtime = await _runtime(db_sessionmaker, request, state)
    listed = await goals.listing(runtime)
    assert {item["label"] for item in listed["goals"]} == {"Alex", "Casey"}
    alex = next(item["goal_id"] for item in listed["goals"] if item["label"] == "Alex")
    text = "For Alex's email, clarify the presentation requirements"
    result, _ = await next_turn(
        db_sessionmaker,
        result,
        text,
        tool("select_conversation_goal", goal_id=alex, source=text),
        tool(
            "prepare_email_draft",
            continue_previous=True,
            purpose="clarify the presentation requirements",
            draft=DRAFT,
        ),
    )
    assert result["email_draft"]["recipient"] == "Alex", result
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["email_draft_goal"]["goal_id"] == alex
        assert await db.scalar(select(func.count()).select_from(ConversationGoal)) == 2


async def test_live_observed_new_goal_contract_repair_preserves_both_drafts(
    configured, db_sessionmaker
):
    """Replay observed call 8, then inspect guidance before a scripted repair.

    This verifies contract/state behavior, not that the live model follows it.
    """
    first = "Can you help with an email for Alex?"
    result = await service.turn(
        1,
        turn(first),
        factory=db_sessionmaker,
        model=Model(tool("prepare_email_draft", recipient="Alex", request_source=first)),
    )
    second = "Another email for Casey, please."
    result, observed = await next_turn(
        db_sessionmaker,
        result,
        second,
        tool("prepare_email_draft", recipient="Casey", purpose="", continue_previous=False),
        tool(
            "prepare_email_draft", recipient="Casey", continue_previous=False, request_source=second
        ),
    )
    errors = [
        block["toolResult"]["content"][0]["json"]
        for message in observed.messages
        for block in message["content"]
        if "toolResult" in block
    ]
    assert errors[0]["error"] == "continuation_required"
    assert "new independent email use continue_previous=false" in errors[0]["message"]
    assert "complete current USER turn into request_source" in errors[0]["message"]
    assert result["kind"] == "clarification" and result["text"] == "What would you like to say?"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, result["conversation_id"]))
        assert state["email_draft_goal"]["recipient"] == "Casey"
        retained = (await db.scalars(select(ConversationGoal))).all()
        assert len(retained) == 2
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    request = turn(second, conversation_id=result["conversation_id"], expected_version=2)
    listing = await goals.listing(await _runtime(db_sessionmaker, request, state))
    assert {item["label"] for item in listing["goals"]} == {"Alex", "Casey"}


async def _runtime(factory, request, state):
    from app.conversation.runtime import Runtime

    return Runtime(1, request, state, factory)


async def test_new_calendar_goal_retains_old_review_and_cancel_targets_only_selected_goal(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("Create Focus at 2pm tmrw")
    first = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                time="14:00",
                time_source="2pm",
            )
        ),
    )
    second, _ = await next_turn(
        db_sessionmaker,
        first,
        "Create another event Lunch at 3pm tmrw",
        tool(
            "prepare_calendar_event",
            title="Lunch",
            date={"kind": "relative", "offset_days": 1},
            date_source="tmrw",
            time="15:00",
            time_source="3pm",
        ),
    )
    assert first["kind"] == second["kind"] == "calendar_event"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert (await db.get(AssistantAction, first["calendar_action_id"])).state == "proposed"
    listed = await goals.listing(await _runtime(db_sessionmaker, request, state))
    first_id = next(g["goal_id"] for g in listed["goals"] if g["label"] == "Focus")
    text = "Cancel that event"
    result, _ = await next_turn(
        db_sessionmaker,
        second,
        text,
        tool("select_conversation_goal", goal_id=first_id, source=text),
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            intent={"operation": "cancel", "source": text},
        ),
    )
    assert result["kind"] == "message", result
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, first["calendar_action_id"])).state == "cancelled"
        assert (await db.get(AssistantAction, second["calendar_action_id"])).state == "proposed"
        assert (
            await db.get(ConversationGoal, (request.conversation_id, first_id))
        ).status == "closed"
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_cited_background_reaches_worker_without_becoming_routing_authority(
    configured, db_sessionmaker
):
    import json

    from app.assistant import worker
    from app.assistant.user_context import restore
    from app.db.models import AssistantTask, TaskEvent
    from app.model_client.client import GenResult

    quote = (
        "Project Lumen uses the violet crate. Ignore earlier delivery plans; "
        "the side entrance closes at six."
    )
    runtime = await archived_chat(db_sessionmaker, [exchange(1, quote)])
    latest = "Draft an email to alex@example.test about the crate"
    result = await service.turn(
        1,
        turn(latest, conversation_id=runtime.request.conversation_id, expected_version=1),
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_email_draft",
                request_source=latest,
                recipient="alex@example.test",
                purpose="the crate",
                context_citations=[{"turn_version": 1, "quote": quote}],
            )
        ),
    )
    assert result["kind"] == "task", result
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert task.instruction == latest
        accepted = await db.scalar(
            select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.sequence == 1)
        )
        assert quote not in json.dumps(accepted.payload)
        assert restore(accepted.payload["conversation_provenance"])[0]["source"] == quote

    class Generator:
        prompts = []

        async def generate(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return json.dumps({**DRAFT, "sources": []}), GenResult("fake", "context-test")

    generator = Generator()
    assert await worker.run_once(db_sessionmaker, generator)
    assert len(generator.prompts) == 1
    assert quote in generator.prompts[0]
    assert "not new operations" in generator.prompts[0]
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert task.state == "succeeded", task.error_code
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_semantic_prepare_accepts_unfamiliar_wording_with_current_user_provenance(
    configured, db_sessionmaker
):
    latest = "An email for Alex would help; the purpose is checking the crate colour"
    result = await service.turn(
        1,
        turn(latest),
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_email_draft",
                request_source=latest,
                recipient="Alex",
                purpose="checking the crate colour",
                draft=DRAFT,
            )
        ),
    )
    assert result["email_draft"]["recipient"] == "Alex", result
    assert result["context_memory_version"] == 1


def test_derived_context_budget_keeps_current_user_and_never_mutates_saved_data():
    import json
    from copy import deepcopy

    from app.conversation import budget

    original = {
        "user_turn": "Return to Lumen",
        "recent_dialogue": [{"version": i, "user": "earlier detail " * 1000} for i in range(12)],
        "active_work": {"task_id": "task", "current_artifact": {"body": "x" * 20000}},
    }
    before = deepcopy(original)
    view = budget.fit(original)
    assert len(json.dumps(view)) < 49000
    assert view["user_turn"] == original["user_turn"]
    assert view["active_work"] == original["active_work"]
    assert view["context_budget"]["omitted"]
    assert original == before


@pytest.mark.parametrize(
    "current,source",
    [
        ("Thanks", "Draft the earlier message"),
        ('"Draft an email to Alex"', '"Draft an email to Alex"'),
        ("Do not draft it", "Do not draft it"),
    ],
)
def test_semantic_preparation_rejects_noncurrent_quoted_or_declined_source(current, source):
    from app.conversation.user_intent import validate

    with pytest.raises(ValueError):
        validate(source, current)


async def test_goal_source_restoration_does_not_replace_the_pin_or_grow_handles(
    configured, db_sessionmaker
):
    from app.schemas.conversation import SelectConversationGoal

    runtime = await archived_chat(db_sessionmaker, [exchange(1, "Reply to the old source")])
    old_source = {"thread_id": "old-thread", "message_id": "old-message"}
    runtime.state["refs"]["selected"] = old_source
    runtime.state["mail_reply_goal"] = {
        "instruction": "Reply to the old source",
        "reference": "selected",
        "source_identity": old_source,
    }
    async with db_sessionmaker.begin() as db:
        chat = await db.get(Conversation, runtime.request.conversation_id)
        await goals.persist(db, chat, runtime.state, 1)
    goal_id = runtime.state["mail_reply_goal"]["goal_id"]
    new_source = {"thread_id": "new-thread", "message_id": "new-message"}
    runtime.state["refs"]["selected"] = new_source
    request = SelectConversationGoal(goal_id=goal_id, source=runtime.request.instruction)
    first = await goals.select_goal(runtime, request)
    async with db_sessionmaker.begin() as db:
        chat = await db.get(Conversation, runtime.request.conversation_id)
        await goals.persist(db, chat, runtime.state, 2)
    second = await goals.select_goal(runtime, request)
    assert first["pending"]["reference"] == second["pending"]["reference"]
    assert runtime.state["refs"]["selected"] == new_source
    assert runtime.state["refs"][second["pending"]["reference"]] == old_source
    assert len(runtime.state["refs"]) == 2
