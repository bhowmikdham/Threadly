import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant import drafting, grounded_answer, summary_quality, worker
from app.db.models import ContextSnapshot, Message
from app.model_client.client import GenResult
from app.planner.intent_router import parse_decision, require_context
from tests.conftest import needs_pg
from tests.test_intent_router import proposal
from tests.test_on_demand_gmail import MID, capture, setup

__all__ = ["setup"]
SOURCE = {
    "messages": [{"message_id": "m1", "body": "Total (incl. GST)\n$14.50", "sent_at": None}],
    "omitted_messages": 0,
    "truncated_messages": 0,
}


@pytest.mark.parametrize(
    "value",
    [
        {"found": True, "quotes": []},
        {"found": False, "quotes": [{"source": 1, "quote": "$14.50"}]},
        {"found": True, "quotes": [{"source": 2, "quote": "$14.50"}]},
        {"found": True, "quotes": [{"source": True, "quote": "$14.50"}]},
        {"found": True, "quotes": [{"source": 1, "quote": "$24.50"}]},
        {"found": True, "quotes": [{"source": 1, "quote": "$14.5"}]},
        {"found": True, "quotes": [{"source": 1, "quote": "$14.50"}] * 2},
        {"found": True, "quotes": [{"source": 1, "quote": "$14.50"}], "to": ["x@example.test"]},
    ],
)
def test_answer_rejects_ungrounded_or_incoherent_results(value):
    with pytest.raises(ValueError):
        grounded_answer.make_artifact(json.dumps(value), "owned", SOURCE)


def test_answer_returns_backend_span_and_explicit_absence():
    value = {"found": True, "quotes": [{"source": 1, "quote": "Total (incl. GST) $14.50"}]}
    a = grounded_answer.make_artifact("```json\n" + json.dumps(value) + "\n```", "owned", SOURCE)
    assert a["content"]["text"] == SOURCE["messages"][0]["body"]
    assert a["evidence"][0]["source_id"] == "m1"
    no = grounded_answer.make_artifact('{"found":false,"quotes":[]}', "owned", SOURCE)
    assert no["content"]["found"] is False and no["evidence"] == []
    assert "selected excerpts" in no["content"]["text"]


def test_summary_fence_preserves_citation_and_budget_checks():
    value = {"overview": "Order confirmed.", "decisions": [], "actions": [], "open_questions": []}
    assert (
        summary_quality.make_artifact("```json\n" + json.dumps(value) + "\n```", "owned", SOURCE)[
            "kind"
        ]
        == "summary"
    )
    for invalid in (
        {**value, "overview": "word " * 81},
        {**value, "actions": [{"text": "Send it", "sources": [2]}]},
    ):
        with pytest.raises(ValueError):
            summary_quality.make_artifact(
                "```json\n" + json.dumps(invalid) + "\n```", "owned", SOURCE
            )


@pytest.mark.parametrize("model_subject", [None, "New subject", "Re: Missing emoji"])
def test_reply_subject_remains_exactly_backend_owned(model_subject):
    original = "Re: Hola 👋 Order 🧾"
    envelope = {
        "to": ["qa@example.test"],
        "cc": [],
        "bcc": [],
        "reply": {
            "subject": original,
            "rfc_message_id": "<x@example.test>",
            "gmail_thread_id": "t",
        },
    }
    claim = SimpleNamespace(
        snapshot=SOURCE,
        context_id="owned",
        draft_input=envelope,
        task_id="task",
        instruction="Draft a reply",
    )
    value = {"body": "Thank you.", "sources": [1], "unresolved_fields": []}
    if model_subject is not None:
        value["subject"] = model_subject
    assert (
        drafting.make_artifact(json.dumps(value), claim, "reply")["content"]["subject"] == original
    )
    with pytest.raises(ValueError):
        drafting.make_artifact(
            json.dumps({**value, "subject": "X\r\nBcc: other@example.test"}), claim, "reply"
        )


def test_resolved_context_cannot_promote_empty_clarification_to_ready():
    route = parse_decision(
        json.dumps(
            proposal(
                status="needs_clarification",
                operations=[],
                output_kind="clarification",
                missing_fields=["source_context"],
                clarification="Which email?",
            )
        )
    )
    with pytest.raises(ApiError) as error:
        require_context(route, has_source=True)
    assert error.value.code == "invalid_route_output"
    route = parse_decision(json.dumps(proposal(operations=["lookup_entity"])))
    assert require_context(route).missing_fields == ["source_context"]


class AnswerModel:
    async def generate(self, prompt, **kwargs):
        if kwargs.get("small"):
            assert '"saved_thread_excerpts": true' in prompt
            assert "PRIVATE-SOURCE-MARKER" not in prompt
            return json.dumps(proposal(operations=["lookup_entity"])), GenResult("fake", "route")
        return json.dumps(
            {"found": True, "quotes": [{"source": 1, "quote": "by Friday"}]}
        ), GenResult("fake", "answer")


@needs_pg
async def test_on_demand_answer_lifecycle_ownership_and_changed_source(
    setup, db_client, auth_headers, db_sessionmaker
):
    context = capture(db_client, auth_headers)
    request = {
        "schema_version": "1.0",
        "request_id": "question-1",
        "instruction": "When is the agenda due?",
        "intent_hint": "other",
        "context_snapshot_id": context["context_snapshot_id"],
        "continuation": None,
    }
    response = db_client.post("/assistant/requests", json=request, headers=auth_headers(1))
    assert response.status_code == 202
    assert (
        db_client.post(
            "/assistant/requests",
            json={**request, "request_id": "foreign"},
            headers=auth_headers(2),
        ).status_code
        == 404
    )
    await worker.run_once(db_sessionmaker, AnswerModel())
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "succeeded", task
    artifact_id = task["artifact_id"]
    a = db_client.get("/assistant/artifacts/" + artifact_id, headers=auth_headers(1)).json()[
        "artifact"
    ]
    assert a["content"]["text"] == "by Friday"
    assert a["evidence"][0]["source_id"] == MID
    assert (
        db_client.get("/assistant/artifacts/" + artifact_id, headers=auth_headers(2)).status_code
        == 404
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        row = await db.get(ContextSnapshot, context["context_snapshot_id"])
        assert row.payload["storage"] == "gmail-reference-1.0" and "messages" not in row.payload
    # Independent new attempt must not publish from the old capture if Gmail changes.
    response = db_client.post(
        "/assistant/requests", json={**request, "request_id": "question-2"}, headers=auth_headers(1)
    )
    setup.text = "The agenda is now due on Monday."
    await worker.run_once(db_sessionmaker, AnswerModel())
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "failed" and task["artifact_id"] is None


@needs_pg
async def test_answer_changed_during_generation_is_not_published(
    setup, db_client, auth_headers, db_sessionmaker
):
    ctx = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "mid-generation",
            "instruction": "When is the agenda due?",
            "intent_hint": "other",
            "context_snapshot_id": ctx["context_snapshot_id"],
            "continuation": None,
        },
    )

    class ChangingModel(AnswerModel):
        async def generate(self, prompt, **kwargs):
            result = await super().generate(prompt, **kwargs)
            if not kwargs.get("small"):
                setup.text = "Changed while inference was running."
            return result

    await worker.run_once(db_sessionmaker, ChangingModel())
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "failed" and task["error_code"] == "source_changed"
    assert task["artifact_id"] is None


def test_reply_policy_is_isolated_from_verified_compose_prompt():
    from app.assistant.summary import digest

    assert (
        digest(drafting.PROMPT)
        == "02b04d01f10c12abdf9e91db3bbc4fcf363af0bde92e4010febb9e65eb70e71d"
    )
    assert drafting.REPLY_PROMPT != drafting.PROMPT
    assert drafting.make_prompt(
        "Write an email", None, {"to": [], "reply": None}, "new"
    ).startswith(drafting.PROMPT)
    reply = {"to": [], "reply": {"subject": "Re: Receipt"}, "reply_message_id": "m1"}
    assert drafting.make_prompt("Draft a reply", SOURCE, reply, "reply").startswith(
        drafting.REPLY_PROMPT
    )
