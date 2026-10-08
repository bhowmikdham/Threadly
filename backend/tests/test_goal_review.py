"""Review identity and domain boundaries, including the exact .6 live failure."""
# ruff: noqa: F401, F811

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.conversation import email_review, goal_review, goals, store
from app.db.models import ActionApproval, ActionJob, AssistantAction, Conversation, ConversationGoal
from tests.test_chat_context_live_regressions import calendar_preview, prepared
from tests.test_conversation import Model, configured, tool
from tests.test_goal_turn_state import owned_runtime
from tests.test_on_demand_gmail import setup
from tools.evaluate_chat_context_followup import step


async def recorded_calendar(factory):
    path = (
        Path(__file__).parents[2]
        / "docs/evaluation/chat-context/context6-resumption-ledger.json.gz"
    )
    calls = json.loads(gzip.decompress(path.read_bytes()))["calls"][6:10]
    previous = None
    for text, call in zip(
        [
            "Put Quiet hour in my diary tomorrow at 2 pm.",
            "Make it 3 pm instead.",
            "Thanks, that's all.",
        ],
        calls,
        strict=False,
    ):
        previous = await step(factory, previous, text, Model(deepcopy(call["response"]["message"])))
    assert previous["text"] == "All right. Take care!"
    return previous, deepcopy(calls[-1]["response"]["message"])


async def test_exact_context6_wrong_domain_call_requires_bound_review(prepared, db_sessionmaker):
    previous, wrong = await recorded_calendar(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous, "Where do I confirm it?")
    pending = deepcopy(r.state["calendar_event_request"])
    async with db_sessionmaker() as db:
        action = await db.get(AssistantAction, pending["action_id"])
        before = (action.state, action.payload_hash, action.version)
    result = await step(
        db_sessionmaker,
        previous,
        r.request.instruction,
        Model(
            wrong,
            tool(
                "review_conversation_goal",
                goal_id=pending["goal_id"],
                source=r.request.instruction,
                presentation="status",
            ),
        ),
    )
    assert result["trace"][0]["status"] == "review_goal_required"
    assert result["calendar_action_id"] == pending["action_id"]
    assert result["calendar_action"]["state"] == "proposed"
    assert "Gmail" not in result["text"] and not result.get("email_draft_review")
    async with db_sessionmaker() as db:
        action = await db.get(AssistantAction, pending["action_id"])
        assert (action.state, action.payload_hash, action.version) == before
        assert (
            store.decode(await db.get(Conversation, previous["conversation_id"]))[
                "calendar_event_request"
            ]
            == pending
        )
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_calendar_only_context_has_no_email_controls(prepared, db_sessionmaker):
    previous = await calendar_preview(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous)
    r.capabilities = {"capabilities": []}
    async with db_sessionmaker() as db:
        assert await email_review.context(r, db) is None


async def test_unbound_review_of_mixed_goals_clarifies_without_switching(prepared, db_sessionmaker):
    previous = prepared[2]["previous"]
    r = await owned_runtime(db_sessionmaker, previous, "Where do I confirm it?")
    await r.context()
    before = deepcopy(r.state)
    result = await email_review.review(r)
    assert result["kind"] == "clarification"
    assert not result.get("email_draft_review") and not result.get("task")
    assert r.state == before


@pytest.mark.parametrize("route", ["email_tool", "email_shortcut", "calendar_status", "promise"])
async def test_parallel_unbound_routes_do_not_choose_in_mixed_chat(
    prepared, db_sessionmaker, route
):
    previous = prepared[2]["previous"]
    text, decision = {
        "email_tool": ("Where do I confirm it?", tool("review_email_draft")),
        "email_shortcut": ("Save it", tool("review_email_draft")),
        "calendar_status": ("Is it done?", tool("respond", kind="message", text="I'll check.")),
        "promise": ("Where is it?", tool("respond", kind="message", text="I will save the draft.")),
    }[route]
    r = await owned_runtime(db_sessionmaker, previous, text)
    before = deepcopy(r.state)
    result = await step(db_sessionmaker, previous, text, Model(decision))
    assert result["kind"] == "clarification"
    assert not any(
        result.get(k) for k in ("calendar_action", "email_draft_review", "task", "email_draft")
    )
    after = await owned_runtime(db_sessionmaker, result)
    for key in ("active_goal", "active_goal_id", "active_task_id", "calendar_event_request"):
        assert after.state.get(key) == before.get(key)


async def test_explicit_mixed_goal_reviews_use_only_the_selected_source(prepared, db_sessionmaker):
    previous = prepared[2]["previous"]
    r = await owned_runtime(db_sessionmaker, previous, "Show the email draft again")
    pending = deepcopy(r.state["calendar_event_request"])
    draft = await step(
        db_sessionmaker,
        previous,
        r.request.instruction,
        Model(
            tool(
                "review_conversation_goal",
                goal_id=prepared[2]["saved_artifact"]["task_id"],
                source=r.request.instruction,
            )
        ),
    )
    assert draft["task"]["artifact_id"] == prepared[2]["saved_artifact"]["artifact_id"]
    assert draft["email_draft_review"]["source"]["kind"] == "task"
    text = "Show the Calendar request instead"
    event = await step(
        db_sessionmaker,
        draft,
        text,
        Model(
            tool(
                "review_conversation_goal",
                goal_id=pending["goal_id"],
                source=text,
            )
        ),
    )
    assert "needs details" in event["text"]
    assert not event.get("email_draft_review") and not event.get("task")
    after = await owned_runtime(db_sessionmaker, event)
    assert after.state["calendar_event_request"] == pending


async def test_unfinished_email_review_asks_only_missing_detail(prepared, db_sessionmaker):
    previous = prepared[0]["previous"]
    r = await owned_runtime(db_sessionmaker, previous, "Show Alex's draft")
    text = r.request.instruction
    response = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "review_conversation_goal",
                goal_id=r.state["email_draft_goal"]["goal_id"],
                source=text,
            )
        ),
    )
    assert response["kind"] == "clarification"
    assert response["text"] == "What would you like to say?"
    assert not response.get("email_draft_review")


@pytest.mark.parametrize(
    "state",
    [
        "proposed",
        "approved",
        "executing",
        "succeeded",
        "failed",
        "outcome_unknown",
        "cancelled",
        "expired",
        "superseded",
        "rejected",
    ],
)
async def test_closed_calendar_review_reads_current_action_without_reopening(
    prepared, db_sessionmaker, state
):
    previous = await calendar_preview(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous, "Show that event's status")
    pending = r.state.pop("calendar_event_request")
    async with db_sessionmaker.begin() as db:
        row = await db.get(ConversationGoal, (previous["conversation_id"], pending["goal_id"]))
        row.status = "closed"
        action = await db.get(AssistantAction, pending["action_id"])
        action.state = state
        before_action = (action.payload_hash, action.version)
        if state == "succeeded":
            action.result = {"provider_event_id": "synthetic-event"}
        before_goal = (row.payload_hash, row.updated_version)
    before = deepcopy(r.state)
    response = await goal_review.review(r, goal_id=pending["goal_id"], source=r.request.instruction)
    assert response["calendar_action"]["state"] == state
    assert not response.get("email_draft_review")
    assert r.state == before
    listed = await goals.listing(r, include_closed=True)
    assert listed["goals"][0]["status"] == "closed"
    async with db_sessionmaker() as db:
        row = await db.get(ConversationGoal, (previous["conversation_id"], pending["goal_id"]))
        assert (row.payload_hash, row.updated_version) == before_goal and row.status == "closed"
        action = await db.get(AssistantAction, pending["action_id"])
        assert (action.payload_hash, action.version) == before_action and action.state == state
        for model in (ActionApproval, ActionJob):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(
    "violation", ["source", "owner", "chat", "missing", "action_owner", "action_chat"]
)
async def test_review_rejects_wrong_identity_without_state_changes(
    prepared, db_sessionmaker, violation
):
    previous = await calendar_preview(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous, "Show that event")
    pending = r.state["calendar_event_request"]
    args = {"goal_id": pending["goal_id"], "source": r.request.instruction}
    if violation == "source":
        args["source"] = "Previous user text"
    elif violation == "owner":
        r.owner = 2
    elif violation == "chat":
        r.request = r.request.model_copy(
            update={"conversation_id": prepared[0]["previous"]["conversation_id"]}
        )
    elif violation == "missing":
        args["goal_id"] = "unknown-goal"
    else:
        async with db_sessionmaker.begin() as db:
            action = await db.get(AssistantAction, pending["action_id"])
            if violation == "action_owner":
                # The owned chat points at someone else's valid action; retain
                # the DB's task/artifact/action ownership constraints.
                chat = await db.get(Conversation, previous["conversation_id"])
                chat.user_id = 2
                r.owner = 2
            else:
                action.source_versions = {
                    **action.source_versions,
                    "conversation_id": prepared[0]["previous"]["conversation_id"],
                }
    before = deepcopy(r.state)
    with pytest.raises(ApiError):
        await goal_review.review(r, **args)
    assert r.state == before


async def test_cancelled_calendar_request_remains_reviewable_after_slot_removed(
    prepared, db_sessionmaker
):
    previous = await calendar_preview(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous)
    goal_id = r.state["calendar_event_request"]["goal_id"]
    text = "Cancel that event"
    cancelled = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "prepare_calendar_event",
                continue_previous=True,
                intent={"operation": "cancel", "source": text},
            )
        ),
    )
    r = await owned_runtime(db_sessionmaker, cancelled)
    assert "calendar_event_request" not in r.state
    text = "What happened to that event?"
    response = await step(
        db_sessionmaker,
        cancelled,
        text,
        Model(
            tool(
                "review_conversation_goal",
                goal_id=goal_id,
                source=text,
                presentation="status",
            )
        ),
    )
    assert response["calendar_action_id"] == previous["calendar_action_id"]
    assert response["calendar_action"]["state"] == "cancelled"
    r = await owned_runtime(db_sessionmaker, response)
    assert "calendar_event_request" not in r.state
    assert (await goals.listing(r, include_closed=True))["goals"][0]["status"] == "closed"


async def test_cleared_calendar_candidate_never_falls_back_to_older_preview(
    prepared, db_sessionmaker
):
    previous = await calendar_preview(db_sessionmaker)
    r = await owned_runtime(db_sessionmaker, previous, "Show that event")
    pending = r.state["calendar_event_request"]
    pending.pop("action_id")
    response = await goal_review.review(r, goal_id=pending["goal_id"], source=r.request.instruction)
    assert "needs details" in response["text"] and not response.get("calendar_action")


async def test_closed_email_is_not_reopened_by_review(prepared, db_sessionmaker):
    r = await owned_runtime(db_sessionmaker, prepared[0]["previous"], "Show Alex's draft")
    goal_id = r.state["email_draft_goal"]["goal_id"]
    async with db_sessionmaker.begin() as db:
        row = await db.get(ConversationGoal, (r.request.conversation_id, goal_id))
        row.status = "closed"
    before = deepcopy(r.state)
    with pytest.raises(ApiError):
        await goal_review.review(r, goal_id=goal_id, source=r.request.instruction)
    assert r.state == before


async def test_calendar_review_does_not_attach_an_email_selected_earlier_in_turn(
    prepared, db_sessionmaker
):
    previous = prepared[2]["previous"]
    r = await owned_runtime(db_sessionmaker, previous)
    text = "Show the Calendar request"
    result = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "select_conversation_goal",
                goal_id=prepared[2]["saved_artifact"]["task_id"],
                source=text,
            ),
            tool(
                "review_conversation_goal",
                goal_id=r.state["calendar_event_request"]["goal_id"],
                source=text,
            ),
        ),
    )
    assert "needs details" in result["text"]
    assert not any(result.get(k) for k in ("task", "email_draft", "email_draft_review"))


async def test_proposal_review_replay_never_restarts_generation(
    prepared, db_sessionmaker, monkeypatch
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.assistant import coordinator
    from app.conversation import service
    from app.db.models import CommandPlan

    previous = prepared[0]["previous"]
    r = await owned_runtime(db_sessionmaker, previous, "Show that proposal")
    plan_id = str(uuid4())
    async with db_sessionmaker.begin() as db:
        db.add(
            CommandPlan(
                id=plan_id,
                user_id=1,
                request_id=plan_id,
                request_hash="a" * 64,
                request={"instruction": "Summarise and draft a reply"},
                release={"workflow": coordinator.RELEASE},
                state="planning",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await db.flush()
        r.state["proposal_id"] = plan_id
        await goals.persist(db, await db.get(Conversation, r.request.conversation_id), r.state, 1)

    async def no_generation(*args, **kwargs):
        raise AssertionError("Review and replay must not restart proposal generation")

    monkeypatch.setattr(coordinator, "interpret", no_generation)
    result = await step(
        db_sessionmaker,
        previous,
        r.request.instruction,
        Model(
            tool(
                "review_conversation_goal",
                goal_id=plan_id,
                source=r.request.instruction,
            )
        ),
    )
    assert result["proposal"]["state"] == "planning"
    replay = await service.hydrate_response(1, result, db_sessionmaker)
    assert replay["proposal"]["state"] == "planning"
    async with db_sessionmaker() as db:
        assert (await db.get(CommandPlan, plan_id)).state == "planning"
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
