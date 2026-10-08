"""Replay the .4 live repair loop, then exercise operation/identity boundaries."""
# ruff: noqa: F401, F811

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.conversation import store
from app.db.models import Conversation, ConversationGoal
from app.schemas.conversation import tool_config
from tests.test_chat_context_live_regressions import prepared
from tests.test_conversation import Model, configured, tool
from tests.test_on_demand_gmail import setup
from tools.evaluate_chat_context_followup import record_state, step


class RecordingModel(Model):
    async def decide(self, system, messages, tools):
        self.last_messages = deepcopy(messages)
        return await super().decide(system, messages, tools)


async def alex_state(factory, seed):
    async with factory() as db:
        state = store.decode(await db.get(Conversation, seed["conversation_id"]))
        identifier = state["email_draft_goal"]["goal_id"]
        row = await db.get(ConversationGoal, (seed["conversation_id"], identifier))
        return identifier, row.payload_hash


async def test_exact_six_call_trace_starts_new_goal_without_reinterpreting_it_as_resume(
    prepared, db_sessionmaker
):
    previous = prepared[0]["previous"]
    alex_id, original_hash = await alex_state(db_sessionmaker, previous)
    path = (
        Path(__file__).resolve().parents[2]
        / "docs/evaluation/chat-context/second-resumed-model-ledger.json.gz"
    )
    recorded = json.loads(gzip.decompress(path.read_bytes()))["calls"][9:15]
    decisions = []
    for call in recorded:
        decision = deepcopy(call["response"]["message"])
        for block in decision["content"]:
            values = block.get("toolUse", {}).get("input", {})
            if values.get("goal_id"):
                values["goal_id"] = alex_id
        decisions.append(decision)
    model = Model(*decisions)
    response = await step(db_sessionmaker, previous, "Another email for Casey, please.", model)
    assert response["kind"] == "clarification"
    assert response["text"] == "What would you like to say?"
    assert len(model.contexts) == 1  # Legacy explicit start needs no correction loop.
    inspected = await record_state(db_sessionmaker, response)
    assert inspected["state"]["email_draft_goal"]["recipient"] == "Casey"
    assert inspected["state"]["email_draft_goal"]["goal_id"] != alex_id
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == original_hash


def test_model_tools_cannot_express_conflicting_continue_flag_and_goal_identity():
    specs = {
        t["toolSpec"]["name"]: t["toolSpec"]["inputSchema"]["json"] for t in tool_config()["tools"]
    }
    assert "prepare_email_draft" not in specs
    assert "goal_id" not in specs["start_email_draft"]["properties"]
    assert "continue_previous" not in specs["start_email_draft"]["properties"]
    assert "goal_id" in specs["continue_email_draft"]["required"]
    assert "continue_previous" not in specs["continue_email_draft"]["properties"]


async def test_repeated_invalid_resume_stops_before_the_six_call_loop(prepared, db_sessionmaker):
    previous = prepared[0]["previous"]
    alex_id, original_hash = await alex_state(db_sessionmaker, previous)
    text = "Another email for Casey, please."
    model = Model(
        *[
            tool(
                "prepare_email_draft",
                continue_previous=True,
                goal_id=alex_id,
                recipient="Casey",
                request_source=text,
            )
            for _ in range(6)
        ]
    )
    response = await step(db_sessionmaker, previous, text, model)
    assert response["error_code"] == "email_draft_not_prepared"
    assert len(model.contexts) <= 2
    assert response["trace"][-1]["reason"] == "new_email_requires_start"
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == original_hash


@pytest.mark.parametrize(
    "text,recipient",
    [
        ("A separate note for Morgan, please.", "Morgan"),
        ("Could you help with another message for Riley?", "Riley"),
    ],
)
async def test_new_tool_starts_varied_independent_goals_without_inheriting_fields(
    prepared, db_sessionmaker, text, recipient
):
    previous = prepared[0]["previous"]
    alex_id, original_hash = await alex_state(db_sessionmaker, previous)
    response = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "start_email_draft",
                request_source=text,
                recipient=recipient,
            )
        ),
    )
    inspected = await record_state(db_sessionmaker, response)
    assert response["kind"] == "clarification" and "say" in response["text"]
    assert inspected["state"]["email_draft_goal"]["recipient"] == recipient
    assert inspected["state"]["email_draft_goal"]["purpose"] == ""
    assert inspected["state"]["email_draft_goal"]["goal_id"] != alex_id
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == original_hash


async def test_new_tool_stale_id_error_repairs_start_instead_of_forcing_resume(
    prepared, db_sessionmaker
):
    previous = prepared[0]["previous"]
    alex_id, _ = await alex_state(db_sessionmaker, previous)
    text = "A separate note for Morgan, please."
    model = RecordingModel(
        tool("start_email_draft", goal_id=alex_id, recipient="Morgan", request_source=text),
        tool("start_email_draft", recipient="Morgan", request_source=text),
    )
    response = await step(db_sessionmaker, previous, text, model)
    repair = model.last_messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert repair["error"] == "new_email_identity_not_allowed"
    assert "Use start_email_draft" in repair["message"]
    assert "set continue_previous=true" not in repair["message"]
    assert response["kind"] == "clarification" and len(model.contexts) == 2


async def test_continue_tool_repairs_missing_identity_without_starting_a_new_goal(
    prepared, db_sessionmaker
):
    previous = prepared[0]["previous"]
    alex_id, _ = await alex_state(db_sessionmaker, previous)
    text = "Ask Alex whether the blue package arrived."
    draft = {
        "subject": "Package",
        "body": "Hi Alex, has the blue package arrived?",
        "unresolved_fields": [],
        "sources": [],
    }
    values = {"request_source": text, "purpose": "whether the blue package arrived", "draft": draft}
    model = RecordingModel(
        tool("continue_email_draft", **values),
        tool("continue_email_draft", goal_id=alex_id, **values),
    )
    response = await step(db_sessionmaker, previous, text, model)
    repair = model.last_messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert repair["error"] == "email_goal_selection_required"
    assert "Use continue_email_draft" in repair["message"]
    inspected = await record_state(db_sessionmaker, response)
    assert len(inspected["goals"]["goals"]) == 1
    assert inspected["state"]["email_draft_goal"]["goal_id"] == alex_id
    assert response["email_draft"]["recipient"] == "Alex"


async def test_explicit_continuation_supports_recipient_and_wording_corrections(
    prepared, db_sessionmaker
):
    previous = prepared[0]["previous"]
    alex_id, _ = await alex_state(db_sessionmaker, previous)
    text = "Ask Alex whether the blue package arrived."
    draft = {
        "subject": "Package",
        "body": "Hi Alex, has the blue package arrived?",
        "unresolved_fields": [],
        "sources": [],
    }
    previous = await step(
        db_sessionmaker,
        previous,
        text,
        Model(
            tool(
                "continue_email_draft",
                goal_id=alex_id,
                request_source=text,
                purpose="whether the blue package arrived",
                draft=draft,
            )
        ),
    )
    for text, recipient, body in [
        ("Actually, this is for Morgan.", "Morgan", "Hi Morgan, has the blue package arrived?"),
        ("Write a shorter version of this email.", "", "Has the blue package arrived?"),
    ]:
        previous = await step(
            db_sessionmaker,
            previous,
            text,
            Model(
                tool(
                    "continue_email_draft",
                    goal_id=alex_id,
                    request_source=text,
                    recipient=recipient,
                    draft={**draft, "body": body},
                )
            ),
        )
        inspected = await record_state(db_sessionmaker, previous)
        assert inspected["state"]["email_draft_goal"]["goal_id"] == alex_id
        assert inspected["state"]["email_draft_goal"]["recipient"] == "Morgan"
        assert len(inspected["goals"]["goals"]) == 1
        assert previous["email_draft"]["body"] == body


async def test_repeated_unknown_goal_is_bounded_without_allocating_or_mutating_work(
    prepared, db_sessionmaker
):
    previous = prepared[0]["previous"]
    alex_id, original_hash = await alex_state(db_sessionmaker, previous)
    text = "Make it shorter."
    model = Model(
        *[
            tool(
                "continue_email_draft",
                request_source=text,
                goal_id="11111111-1111-4111-8111-111111111111",
            )
            for _ in range(6)
        ]
    )
    response = await step(db_sessionmaker, previous, text, model)
    assert response["error_code"] == "email_draft_not_prepared"
    assert len(model.contexts) == 2
    assert response["trace"][-1]["reason"] == "conversation_goal_missing"
    inspected = await record_state(db_sessionmaker, response)
    assert len(inspected["goals"]["goals"]) == 1
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == original_hash


async def test_distinct_invalid_operations_share_a_three_error_ceiling(prepared, db_sessionmaker):
    previous = prepared[0]["previous"]
    alex_id, original_hash = await alex_state(db_sessionmaker, previous)
    text = "A separate note for Morgan, please."
    model = Model(
        tool("start_email_draft", recipient="Morgan"),
        tool("start_email_draft", recipient="Morgan", request_source=text, goal_id=alex_id),
        tool("continue_email_draft", recipient="Morgan", request_source=text),
    )
    response = await step(db_sessionmaker, previous, text, model)
    assert response["error_code"] == "email_draft_not_prepared"
    assert len(model.contexts) == 3
    assert {item["reason"] for item in response["trace"] if item.get("status") == "invalid"} == {
        "email_request_source_required",
        "new_email_identity_not_allowed",
        "email_goal_selection_required",
    }
    async with db_sessionmaker() as db:
        assert (
            await db.get(ConversationGoal, (previous["conversation_id"], alex_id))
        ).payload_hash == original_hash
