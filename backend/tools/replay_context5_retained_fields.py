"""Offline acceptance replay of third-diagnostic calls 1–3.

Outside default discovery; run explicitly against the owned disposable test DB.
No live model, auth discovery or diagnostic DB reuse. Also imported by the suite.
"""
# ruff: noqa: F401, F811

import gzip
import json
from copy import deepcopy
from pathlib import Path

from app.conversation import goals
from tests.conftest import db_engine, db_sessionmaker
from tests.test_chat_context_live_regressions import prepared
from tests.test_conversation import Model, configured
from tests.test_on_demand_gmail import setup
from tools.evaluate_chat_context_followup import step


async def test_same_turn_repair_retains_validated_purpose(prepared, db_sessionmaker, monkeypatch):
    previous = prepared[0]["previous"]
    path = Path(__file__).parents[2] / "docs/evaluation/chat-context/context5-model-ledger.json.gz"
    calls = json.loads(gzip.decompress(path.read_bytes()))["calls"]
    decision = deepcopy(calls[0]["response"]["message"])
    previous = await step(
        db_sessionmaker, previous, "Another email for Casey, please.", Model(decision)
    )
    # The exact provider goal ID was PII-masked in the recorded wire payload.
    # Replace only that identity with this fixture's owned Alex goal.
    from sqlalchemy import select

    from app.auth.crypto import decrypt_token
    from app.db.models import ConversationGoal

    async with db_sessionmaker() as db:
        rows = (
            await db.scalars(
                select(ConversationGoal).where(
                    ConversationGoal.conversation_id == previous["conversation_id"]
                )
            )
        ).all()
        alex = next(r for r in rows if json.loads(decrypt_token(r.payload_enc))["label"] == "Alex")
    decisions = [deepcopy(call["response"]["message"]) for call in calls[1:3]]
    for decision in decisions:
        for block in decision["content"]:
            if use := block.get("toolUse"):
                use["input"]["goal_id"] = alex.goal_id
    original, selection_states = goals.select_goal, []

    async def observed(runtime, args, **kwargs):
        before = deepcopy(runtime.state.get("email_draft_goal"))
        result = await original(runtime, args, **kwargs)
        selection_states.append(
            {"before": before, "after": deepcopy(runtime.state.get("email_draft_goal"))}
        )
        return result

    monkeypatch.setattr(goals, "select_goal", observed)
    response = await step(
        db_sessionmaker,
        previous,
        "Back to Alex: ask whether the sapphire crate has arrived.",
        Model(*decisions),
    )
    print(json.dumps({"selection_states": selection_states, "response": response}, indent=2))
    assert response.get("email_draft"), "Validated purpose must survive draft-text repair"
