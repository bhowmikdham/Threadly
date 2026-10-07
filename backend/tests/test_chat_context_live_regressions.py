"""Exact second-diagnostic failures, replayed with scripted models and real local DB."""
# ruff: noqa: F811

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import permissions
from app.calendar import service as calendar_service
from app.config import get_settings
from app.conversation import service, store
from app.db.models import ActionJob, Conversation, ConversationGoal
from tests.test_conversation import Model, configured, tool  # noqa: F401
from tests.test_on_demand_gmail import setup  # noqa: F401
from tools.evaluate_chat_context_followup import (
    install_calendar_fake,
    prepare_calendar_account,
    record_state,
    scenarios,
    seed_cases,
    step,
)


@pytest.fixture()
async def prepared(configured, db_sessionmaker, monkeypatch):
    from botocore.client import BaseClient

    def no_aws(*args, **kwargs):
        raise AssertionError("Recorded regressions never call AWS")

    monkeypatch.setattr(BaseClient, "_make_api_call", no_aws)
    monkeypatch.setenv("CALENDAR_WRITES_ENABLED", "true")
    monkeypatch.setenv("WRITE_PILOT_USER_IDS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(permissions, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(calendar_service, "get_session_factory", lambda: db_sessionmaker)
    await prepare_calendar_account(db_sessionmaker)
    calls = []
    install_calendar_fake(monkeypatch, calls)
    return await seed_cases(db_sessionmaker)


async def alex_and_casey(factory, seeds):
    previous = seeds[0]["previous"]
    async with factory() as db:
        alex_id = store.decode(await db.get(Conversation, previous["conversation_id"]))[
            "email_draft_goal"
        ]["goal_id"]
    text = "Another email for Casey, please."
    casey = await step(
        factory,
        previous,
        text,
        Model(
            tool(
                "prepare_email_draft",
                recipient="Casey",
                request_source=text,
            )
        ),
    )
    async with factory() as db:
        casey_id = store.decode(await db.get(Conversation, casey["conversation_id"]))[
            "email_draft_goal"
        ]["goal_id"]
    text = "Back to Alex: ask whether the sapphire crate has arrived."
    alex = await step(
        factory,
        casey,
        text,
        Model(
            tool("select_conversation_goal", goal_id=alex_id, source=text),
            tool(
                "prepare_email_draft",
                recipient="Alex",
                purpose="ask whether the sapphire crate has arrived",
                continue_previous=True,
                request_source=text,
                draft={
                    "subject": "Has the sapphire crate arrived?",
                    "body": "Hi Alex, has it arrived?",
                    "unresolved_fields": [],
                    "sources": [],
                },
            ),
        ),
    )
    return alex, alex_id, casey_id


CASEY = {
    "recipient": "Casey",
    "purpose": "thank them for the map",
    "continue_previous": True,
    "request_source": "Now Casey's one: thank them for the map.",
    "draft": {
        "subject": "Thank you for the map",
        "body": "Hi Casey, thank you for the map.",
        "unresolved_fields": [],
        "sources": [],
    },
}


async def test_recorded_casey_return_cannot_overwrite_alex_without_goal_binding(
    prepared, db_sessionmaker
):
    previous, alex_id, casey_id = await alex_and_casey(db_sessionmaker, prepared)
    async with db_sessionmaker() as db:
        before = deepcopy(
            store.decode(await db.get(Conversation, previous["conversation_id"]))[
                "email_draft_goal"
            ]
        )
        alex_hash = (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash
    response = await step(
        db_sessionmaker,
        previous,
        CASEY["request_source"],
        Model(
            tool("prepare_email_draft", **CASEY),  # The exact problematic live call.
            tool("select_conversation_goal", goal_id=casey_id, source=CASEY["request_source"]),
            tool("prepare_email_draft", **CASEY),
        ),
    )
    assert response["trace"][0].get("reason") == "email_goal_selection_required"
    inspected = await record_state(db_sessionmaker, response)
    labels = {g["goal_id"]: g["label"] for g in inspected["goals"]["goals"]}
    assert labels == {alex_id: "Alex", casey_id: "Casey"}
    assert inspected["state"]["email_draft_goal"]["goal_id"] == casey_id
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == alex_hash
        assert before["recipient"] == "Alex"
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_recorded_review_tool_opens_actual_saved_draft_and_preserves_calendar(
    prepared, db_sessionmaker
):
    seed = prepared[2]
    response = await step(
        db_sessionmaker,
        seed["previous"],
        scenarios()[2]["turns"][0],
        Model(
            tool("read_email", reference="context-1"),
            tool(
                "review_email_draft"
            ),  # Exact observed tool choice must still surface reviewable work.
        ),
    )
    assert response.get("task_id") == seed["saved_artifact"]["task_id"]
    assert response["task"]["artifact_id"] == seed["saved_artifact"]["artifact_id"]
    assert "Enable draft creation" not in response["text"]
    inspected = await record_state(db_sessionmaker, response)
    assert inspected["state"]["active_goal"] == "saved_task"
    assert inspected["state"]["calendar_event_request"]["arguments"]["title"] == "Focus"
    assert inspected["task"]["artifact"] == seed["saved_artifact"]["artifact"]


async def calendar_preview(factory):
    text = scenarios()[1]["turns"][0]
    return await step(
        factory,
        None,
        text,
        Model(
            tool(
                "prepare_calendar_event",
                title="Quiet hour",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
                time="14:00",
                time_source="2 pm",
            )
        ),
    )


async def test_recorded_closing_turn_cannot_invent_calendar_save_controls(
    prepared, db_sessionmaker
):
    previous = await calendar_preview(db_sessionmaker)
    response = await step(
        db_sessionmaker,
        previous,
        "Thanks, that's all.",
        Model(
            tool(
                "respond",
                kind="message",
                text='Got it. Your "Quiet hour" event is ready for tomorrow '
                "at 3 pm—just click Create draft to save it to your calendar.",
            )
        ),
    )
    assert response["text"] == "All right. Take care!"
    assert not response.get("calendar_action_id")


async def test_calendar_control_guidance_comes_from_current_owned_action(prepared, db_sessionmaker):
    previous = await calendar_preview(db_sessionmaker)
    response = await step(
        db_sessionmaker,
        previous,
        "What should I press now?",
        Model(
            tool(
                "respond",
                kind="message",
                text="Click Create draft to save your event to Calendar.",
            )
        ),
    )
    assert response.get("calendar_action_id") == previous["calendar_action_id"]
    assert response["calendar_action"]["state"] == "proposed"
    assert "Create draft" not in response["text"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_inline_goal_binding_selects_casey_without_an_extra_model_call(
    prepared, db_sessionmaker
):
    previous, alex_id, casey_id = await alex_and_casey(db_sessionmaker, prepared)
    response = await step(
        db_sessionmaker,
        previous,
        CASEY["request_source"],
        Model(
            tool(
                "prepare_email_draft",
                goal_id=casey_id,
                **CASEY,
            )
        ),
    )
    inspected = await record_state(db_sessionmaker, response)
    assert inspected["state"]["email_draft_goal"]["goal_id"] == casey_id
    assert {g["goal_id"]: g["label"] for g in inspected["goals"]["goals"]} == {
        alex_id: "Alex",
        casey_id: "Casey",
    }
    assert response["trace"] == [{"tool": "prepare_email_draft", "status": "ok"}]


async def test_explicit_goal_binding_allows_an_intentional_recipient_revision(
    prepared, db_sessionmaker
):
    previous, alex_id, casey_id = await alex_and_casey(db_sessionmaker, prepared)
    text = "Change Alex's recipient to Jordan"
    response = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "prepare_email_draft",
                goal_id=alex_id,
                continue_previous=True,
                request_source=text,
                recipient="Jordan",
                draft={
                    "subject": "Has the sapphire crate arrived?",
                    "body": "Hi Jordan, has the sapphire crate arrived?",
                    "unresolved_fields": [],
                    "sources": [],
                },
            )
        ),
    )
    inspected = await record_state(db_sessionmaker, response)
    assert inspected["state"]["email_draft_goal"]["goal_id"] == alex_id
    assert inspected["state"]["email_draft_goal"]["recipient"] == "Jordan"
    assert (
        next(g for g in inspected["goals"]["goals"] if g["goal_id"] == casey_id)["label"] == "Casey"
    )


@pytest.mark.parametrize("violation", ["other_chat", "other_owner", "source", "cancel_ambiguous"])
async def test_goal_binding_rejects_scope_errors_without_mutating_state(
    prepared, db_sessionmaker, violation
):
    from app.conversation.runtime import Runtime
    from app.schemas.conversation import PrepareEmailDraft
    from tests.test_calendar_creation import turn

    previous, alex_id, casey_id = await alex_and_casey(db_sessionmaker, prepared)
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, previous["conversation_id"]))
    text = CASEY["request_source"]
    values = {**CASEY, "goal_id": casey_id}
    owner = 1
    if violation == "other_chat":
        values["goal_id"] = prepared[2]["saved_artifact"]["task_id"]
    elif violation == "other_owner":
        owner = 2
    elif violation == "source":
        values["request_source"] = "A different instruction"
    else:
        text = "Never mind, cancel that email"
        values = {"continue_previous": True}
    runtime = Runtime(
        owner, turn(text, conversation_id=previous["conversation_id"]), state, db_sessionmaker
    )
    before = deepcopy(state)
    with pytest.raises((ApiError, ValueError)) as error:
        await runtime.call("prepare_email_draft", PrepareEmailDraft(**values))
    if violation in {"other_chat", "other_owner"}:
        assert error.value.code == {
            "other_chat": "conversation_goal_missing",
            "other_owner": "conversation_not_found",
        }[violation]
    elif violation == "cancel_ambiguous":
        assert error.value.reason == "email_goal_selection_required"
    else:
        assert str(error.value) == "Copy the complete current USER turn as request_source"
    assert state == before


async def test_owned_calendar_goal_cannot_be_used_as_an_email_target(prepared, db_sessionmaker):
    from app.conversation.runtime import Runtime
    from app.schemas.conversation import PrepareEmailDraft
    from tests.test_calendar_creation import turn

    previous, _, _ = await alex_and_casey(db_sessionmaker, prepared)
    text = "Create Focus tomorrow"
    response = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
            )
        ),
    )
    inspected = await record_state(db_sessionmaker, response)
    state = inspected["state"]
    identifier = state["calendar_event_request"]["goal_id"]
    runtime = Runtime(
        1,
        turn(CASEY["request_source"], conversation_id=previous["conversation_id"]),
        state,
        db_sessionmaker,
    )
    before = deepcopy(state)
    with pytest.raises(ApiError) as error:
        await runtime.call("prepare_email_draft", PrepareEmailDraft(goal_id=identifier, **CASEY))
    assert error.value.code == "conversation_goal_kind"
    assert state == before


async def test_reopened_task_replays_existing_artifact_and_rejects_other_owner(
    prepared, db_sessionmaker
):
    from app.api.errors import ApiError
    from app.assistant import worker
    from tests.test_calendar_creation import turn
    from tests.test_on_demand_gmail import Model as Generator

    seed = prepared[2]
    request = turn(
        scenarios()[2]["turns"][0],
        conversation_id=seed["previous"]["conversation_id"],
        expected_version=seed["previous"]["version"],
    )
    response = await service.turn(
        1, request, factory=db_sessionmaker, model=Model(tool("review_email_draft"))
    )
    replay = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert replay["task_id"] == response["task_id"] == seed["saved_artifact"]["task_id"]
    assert replay["task"]["artifact_id"] == seed["saved_artifact"]["artifact_id"]
    assert not await worker.run_once(db_sessionmaker, Generator())
    with pytest.raises(ApiError) as error:
        await service.turn(2, request, factory=db_sessionmaker, model=Model())
    assert error.value.status == 404


async def test_reopening_refreshes_stale_revision_before_reporting_save_status(
    prepared, db_sessionmaker, monkeypatch
):
    from app.actions import gmail_draft
    from app.assistant import draft_review
    from app.conversation import email_review
    from app.conversation.runtime import Runtime
    from app.db.models import ArtifactRevision
    from app.schemas.draft_review import DraftRecipients, EditDraftRequest
    from tests.test_calendar_creation import turn

    seed = prepared[2]
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, seed["previous"]["conversation_id"]))
        original = await db.get(ArtifactRevision, seed["saved_artifact"]["artifact_id"])
    runtime = Runtime(
        1,
        turn(scenarios()[2]["turns"][0], conversation_id=seed["previous"]["conversation_id"]),
        state,
        db_sessionmaker,
    )
    runtime.artifact = original
    runtime.capabilities = {"capabilities": []}
    async with db_sessionmaker.begin() as db:
        _, revised = await draft_review.edit(
            db,
            1,
            original.task_id,
            EditDraftRequest(
                request_id="edit-after-context-loaded",
                expected_revision=original.revision,
                subject=original.payload["content"]["subject"],
                body="Please send the updated agenda.",
                recipients=DraftRecipients(to=["sender@example.test"]),
                unresolved_fields=[],
            ),
        )

    async def old_receipt(*args):
        return {"state": "succeeded", "source_artifact_id": original.id, "save_id": "old-save"}

    monkeypatch.setattr(gmail_draft, "lookup", old_receipt)
    result = await email_review.review(runtime, presentation="open")
    assert result["task"]["artifact_id"] == revised.id != original.id
    assert runtime.artifact.payload["content"]["body"] == "Please send the updated agenda."
    assert result["email_draft_review"]["save_status"] == "earlier_revision"
    assert state["calendar_event_request"]["arguments"]["title"] == "Focus"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


@pytest.mark.parametrize("state", ["approved", "succeeded", "failed", "outcome_unknown"])
async def test_calendar_advice_uses_actual_saved_status_without_dispatch(
    prepared, db_sessionmaker, state
):
    from app.db.models import AssistantAction

    previous = await calendar_preview(db_sessionmaker)
    async with db_sessionmaker.begin() as db:
        action = await db.get(AssistantAction, previous["calendar_action_id"])
        # Synthetic state only: no action worker or approval operation is called.
        action.state = state
        if state == "succeeded":
            action.result = {"provider_event_id": "synthetic-event"}
    response = await step(
        db_sessionmaker,
        previous,
        "What should I press now?",
        Model(
            tool(
                "respond",
                kind="message",
                text="Click Create draft to save your event to Calendar.",
            )
        ),
    )
    assert response["calendar_action"]["state"] == state
    assert "Create draft" not in response["text"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
