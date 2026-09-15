"""Contract/release replay, not a claim of live Haiku quality improvement."""

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.assistant import routing, summary, summary_policy, summary_quality, tasks, ui_routing
from app.assistant.ui_context import capture_view
from app.assistant.worker import run_once
from app.db.models import ArtifactRevision, AssistantTask
from app.schemas.ui_context import UIContextSnapshotRequest
from app.workflows import registry
from app.workflows.bedrock_flows import FlowInvoker
from app.workflows.evaluate_summary import evaluate, snapshot
from tests.conftest import needs_pg
from tests.test_durable_tasks import FakeModel, create_task, mailbox, request
from tests.test_ui_context import capture_request, visible_mailbox
from tests.test_workflow_runtime import TARGET, FakeSdk, complete, configure, output

__all__ = ["mailbox", "visible_mailbox", "configure"]
FIXTURES = json.loads((Path(__file__).parent / "fixtures/summary_quality_v1.json").read_text())
GOOD = FIXTURES["cases"][0]["expected"]


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda c: c["id"])
def test_human_reference_examples_fit_concise_contract(case):
    artifact = summary_quality.make_artifact(json.dumps(case["expected"]), "saved", snapshot(case))
    assert artifact["content"]["overview"] == case["expected"]["overview"]
    assert "assumptions" not in artifact["content"]
    assert all(e["source_id"].startswith("fixture-") for e in artifact["evidence"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: {**x, "overview": "word " * 81},
        lambda x: {**x, "actions": [{"text": "word " * 36, "sources": [1]}]},
        lambda x: {**x, "decisions": [{"text": f"Decision {i}", "sources": [1]} for i in range(4)]},
        lambda x: {**x, "open_questions": ["one?", "two?", "three?"]},
        lambda x: {**x, "open_questions": [" "]},
        lambda x: {**x, "open_questions": [x["actions"][0]["text"]]},
        lambda x: {**x, "assumptions": ["Both invoices have the same owner."]},
        lambda x: {**x, "evidence_ids": ["message_1"]},
        lambda x: {**x, "actions": [{"text": "Check the payment", "sources": [2]}]},
        lambda x: {**x, "actions": [{"text": "Check the payment", "sources": [1, 1]}]},
    ],
)
def test_verbose_repeated_and_invented_fields_fail_closed(mutate):
    with pytest.raises(ValueError):
        summary_quality.make_artifact(
            json.dumps(mutate(copy.deepcopy(GOOD))), "saved", snapshot(FIXTURES["cases"][0])
        )


@pytest.mark.parametrize("text", ["```json\n{}\n```", '{"overview":"one","overview":"two"}', "[]"])
def test_fences_duplicate_keys_and_wrong_shape_rejected(text):
    with pytest.raises(ValueError):
        summary_quality.make_artifact(text, "saved", snapshot(FIXTURES["cases"][0]))


def test_total_word_limit_is_enforced_across_fields():
    value = {
        "overview": "overview " * 80,
        "decisions": [],
        "actions": [{"text": f"{i} " + "action " * 34, "sources": [1]} for i in range(3)],
        "open_questions": [],
    }
    with pytest.raises(ValueError, match="total word budget"):
        summary_quality.make_artifact(json.dumps(value), "saved", snapshot(FIXTURES["cases"][0]))


def test_prompt_preserves_source_order_and_avoids_provider_ids():
    source = snapshot(FIXTURES["cases"][1])
    prompt = summary_quality.make_prompt(source, "Summarise briefly")
    model_input = json.loads(prompt.split("\nSOURCE_JSON:\n")[1])
    assert [m["number"] for m in model_input] == [1, 2, 3]
    assert model_input[-1]["body"].startswith("Supplier: Payment received")
    assert "fixture-1" not in prompt
    assert "Summarise briefly" in prompt


@needs_pg
async def test_new_task_uses_quality_prompt_and_old_task_keeps_original(db_sessionmaker, mailbox):
    new_id, context_id = await create_task(db_sessionmaker, mailbox[0], "new")
    async with db_sessionmaker.begin() as session:
        old = await tasks.submit(session, mailbox[0], request(context_id, "old"))
        old.release = routing.release_manifest()
        old_id = old.id
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    await run_once(db_sessionmaker, model)
    assert model.calls[0][0].startswith(summary_policy.PROMPT)
    assert summary.PROMPT in model.calls[1][0]
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, new_id)).release[
            "workflow"
        ] == summary_quality.RELEASE
        assert (await session.get(AssistantTask, old_id)).release == routing.release_manifest()


@needs_pg
async def test_verbose_output_is_not_published_from_new_task(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    model = FakeModel(output=json.dumps({**GOOD, "overview": "word " * 81}))
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.error_code == "invalid_summary_output" and task.state == "failed"
        assert (
            await session.scalar(
                select(ArtifactRevision).where(ArtifactRevision.task_id == task_id)
            )
            is None
        )


@needs_pg
async def test_old_verbose_summary_still_uses_historical_validator(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, task_id)
        task.release = routing.release_manifest()
    await run_once(
        db_sessionmaker, FakeModel(output=json.dumps({**GOOD, "overview": "word " * 81}))
    )
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).state == "succeeded"


@needs_pg
async def test_quality_change_fails_saved_release_without_fallback(
    db_sessionmaker, mailbox, monkeypatch
):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    monkeypatch.setattr(summary_policy, "PROMPT", "different prompt")
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).error_code == "release_unavailable"
    assert not model.calls


@needs_pg
async def test_flow_summary_uses_quality_contract_and_keeps_ui_selection(
    db_sessionmaker,
    visible_mailbox,
    configure,
):
    configure()
    async with db_sessionmaker.begin() as session:
        context = await capture_view(
            session, visible_mailbox[0], UIContextSnapshotRequest.model_validate(capture_request())
        )
        task = await tasks.submit(
            session,
            visible_mailbox[0],
            request(context.id, instruction="Summarise the third message"),
        )
        assert task.release["workflow"] == ui_routing.RELEASE
        assert task.release["base_release"]["workflow"] == summary_quality.RELEASE
        assert task.release["base_release"]["base_release"]["workflow"] == registry.RELEASE
        task_id = task.id
    sdk = FakeSdk()
    await run_once(db_sessionmaker, FakeModel(), FlowInvoker(lambda _s, _r: sdk))
    prompt = sdk.invocations[0]["inputs"][0]["content"]["document"]
    assert prompt.startswith(summary_policy.PROMPT)
    assert "Thanks for clarifying" in prompt and "Third chronologically" not in prompt
    async with db_sessionmaker() as session:
        artifact = await session.scalar(
            select(ArtifactRevision).where(ArtifactRevision.task_id == task_id)
        )
        assert [e["source_id"] for e in artifact.payload["evidence"]] == ["m2"]


async def test_evaluation_contract_pass_is_not_marked_quality_pass():
    fixtures = {**FIXTURES, "cases": [FIXTURES["cases"][0]]}
    sdk = FakeSdk(events=[output(json.dumps(GOOD)), complete()])
    report = await evaluate(
        registry.FlowEntry.model_validate(TARGET), fixtures, FlowInvoker(lambda _s, _r: sdk)
    )
    assert report["contract_passed"] == 1 and report["total"] == 1
    assert report["language_quality_review"] == "pending"
    assert report["cases"][0]["quality_review"] == "pending"
    assert not report["production_approved"]


ROOM_FAILURE = {
    "overview": (
        "Workshop date moved to Tuesday; Room A or Room B booking status "
        "unconfirmed and awaiting reply."
    ),
    "decisions": [],
    "actions": [
        {
            "text": "Confirm which room (A or B) is booked for the rescheduled Tuesday workshop.",
            "sources": [1],
        }
    ],
    "open_questions": [
        {"text": "Is Room A or Room B booked for the Tuesday workshop?", "sources": [1]}
    ],
}


def test_observed_room_failure_is_rejected_at_question_type():
    from pydantic import ValidationError

    case = next(c for c in FIXTURES["cases"] if c["id"] == "unanswered-material-question")
    with pytest.raises(ValidationError) as error:
        summary_quality.make_artifact(json.dumps(ROOM_FAILURE), "saved", snapshot(case))
    assert any(
        e["loc"] == ("open_questions", 0) and e["type"] == "string_type"
        for e in error.value.errors()
    )


@needs_pg
async def test_observed_room_failure_does_not_publish_or_retry(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    model = FakeModel(output=json.dumps(ROOM_FAILURE))
    await run_once(db_sessionmaker, model)
    assert not await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "failed" and task.error_code == "invalid_summary_output"
        assert (
            await session.scalar(
                select(ArtifactRevision).where(ArtifactRevision.task_id == task_id)
            )
            is None
        )
    assert len(model.calls) == 1


@pytest.mark.parametrize("version", ["1.0.0", "1.0.1"])
@needs_pg
async def test_previous_quality_release_replays_its_original_prompt(
    db_sessionmaker, mailbox, version
):
    from app.assistant import summary_policy_v1, summary_policy_v1_0_1

    policy = summary_policy_v1 if version == "1.0.0" else summary_policy_v1_0_1

    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, task_id)
        task.release = {
            **task.release,
            "contract_hash": summary_quality.contract_hash(policy),
        }
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    assert model.calls[0][0].startswith(policy.PROMPT)
    assert "CONTENT SELECTION RULES" not in model.calls[0][0]
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).state == "succeeded"


def test_question_fix_keeps_the_output_contract_and_changes_prompt_identity():
    from app.assistant import summary_policy_v1

    assert summary_policy.VERSION == "summary-quality-1.0.2"
    assert summary_quality.contract_hash() != summary_quality.contract_hash(summary_policy_v1)
    # The actual populated string example is valid, rather than a permissive schema change.
    result = {
        "overview": "Delivery address is unconfirmed.",
        "decisions": [],
        "actions": [],
        "open_questions": ["Which delivery address should be used?"],
    }
    summary_quality.make_artifact(json.dumps(result), "saved", snapshot(FIXTURES["cases"][0]))
