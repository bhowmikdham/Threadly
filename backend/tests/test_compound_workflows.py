"""Explicit compound templates through real PostgreSQL, with synthetic model output."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.assistant import draft_review, steps, tasks
from app.assistant.context import capture_thread
from app.assistant.worker import run_once
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantStep,
    AssistantTask,
    DraftReview,
    Message,
    TaskEvent,
    Thread,
)
from app.model_client.client import GenResult
from app.model_client.providers import ProviderError
from app.schemas.compound import CompoundRequest
from app.schemas.draft_review import ReviewDraftRequest
from tests.conftest import needs_pg
from tests.test_draft_review import edit_request
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]

FIXTURE = json.loads((Path(__file__).parent / "fixtures/compound_templates_v1.json").read_text())
SUMMARY = FIXTURE["summary"]
DRAFT = FIXTURE["draft"]


def test_versioned_replay_contract():
    assert FIXTURE["release"] == steps.RELEASE
    assert FIXTURE["contract_hash"] == steps.contract_hash()
    assert FIXTURE["live_model_evaluated"] is False


class PairModel:
    def __init__(self, fail_draft=False, invalid_summary=False, invalid_draft=False):
        self.calls = []
        self.fail_draft = fail_draft
        self.invalid_summary = invalid_summary
        self.invalid_draft = invalid_draft

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        if "DRAFT_REQUEST_JSON" in prompt:
            if self.fail_draft:
                raise ProviderError("PRIVATE_PROVIDER_DETAIL")
            output = {**DRAFT}
            if '"mode": "reply"' in prompt:
                output["subject"] = "Re: Release"
            if self.invalid_draft:
                output["sources"] = [900]
        else:
            output = {**SUMMARY}
            if self.invalid_summary:
                output["actions"] = [{"text": "Invented action", "sources": [900]}]
        return json.dumps(output), GenResult("fake", "compound-fixture")


def compound(context_id, reply=False, include=True, key="compound-1", **changes):
    return CompoundRequest(
        **{
            "schema_version": "1.0",
            "request_id": key,
            "context_snapshot_id": context_id,
            "template": "summary_then_reply" if reply else "summary_then_compose",
            "summary_in_draft": include,
            "draft_options": {
                "to": ["recipient@example.test"],
                "bcc": ["private@example.test"],
                "reply_message_id": "m1" if reply else None,
            },
            "draft_instruction": "Write a concise update. Do not send it.",
            **changes,
        }
    )


async def capture(factory, owner):
    async with factory.begin() as session:
        await session.execute(
            update(Message).values(
                subject="Release",
                reply_metadata={"headers": {"message-id": ["<message@example.test>"]}},
            )
        )
        return (await capture_thread(session, owner, "thread-one")).id


async def submit(factory, owner, **kwargs):
    context = kwargs.pop("context_id", None) or await capture(factory, owner)
    request = compound(context, **kwargs)
    async with factory.begin() as session:
        task = await tasks.submit(session, owner, request.as_request(), compound=request)
        return task.id, request


async def saved(factory, tid):
    async with factory() as session:
        task = await session.get(AssistantTask, tid)
        artifacts = (
            await session.scalars(
                select(ArtifactRevision)
                .where(ArtifactRevision.task_id == tid)
                .order_by(ArtifactRevision.stream_key, ArtifactRevision.revision)
            )
        ).all()
        parts = (
            await session.scalars(
                select(AssistantStep)
                .where(AssistantStep.task_id == tid)
                .order_by(AssistantStep.ordinal)
            )
        ).all()
        return task, artifacts, parts


async def make_due(factory, tid):
    async with factory.begin() as session:
        await session.execute(
            update(AssistantJob)
            .where(AssistantJob.task_id == tid)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )


@pytest.mark.parametrize(
    "reply,include",
    [(c["template"] == "summary_then_reply", c["summary_in_draft"]) for c in FIXTURE["cases"]],
    ids=[c["id"] for c in FIXTURE["cases"]],
)
@needs_pg
async def test_two_outputs_final_pointer_and_draft_history(
    db_sessionmaker, mailbox, db_client, auth_headers, reply, include
):
    owner = mailbox[0]
    context = await capture(db_sessionmaker, owner)
    req = compound(context, reply=reply, include=include)
    headers = auth_headers(owner)
    response = db_client.post(
        "/assistant/compound-requests", headers=headers, json=req.model_dump()
    )
    assert response.status_code == 202, response.text
    tid = response.json()["task_id"]
    assert response.json()["compound"]["total_steps"] == 2
    assert response.json()["artifact_id"] is None
    assert (
        db_client.post(
            "/assistant/compound-requests", headers=headers, json=req.model_dump()
        ).json()["task_id"]
        == tid
    )
    model = PairModel()
    assert await run_once(db_sessionmaker, model)
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "succeeded" and len(artifacts) == 2 and len(parts) == 2
    summary = next(a for a in artifacts if a.stream_key == "summary")
    draft = next(a for a in artifacts if a.stream_key == "result")
    assert task.final_artifact_id == draft.id and draft.revision == summary.revision == 1
    assert parts[0].attempts == parts[1].attempts == 1
    assert len(model.calls) == 2  # no classifier call for explicitly selected templates
    assert ("DERIVED_SUMMARY_JSON" in model.calls[1]) == include
    assert "private@example.test" not in "\n".join(model.calls)
    assert draft.provenance["dependency_artifact_ids"] == ([summary.id] if include else [])
    data = db_client.get(f"/assistant/tasks/{tid}", headers=headers).json()
    assert data["artifact_id"] == draft.id and data["compound"]["completed_steps"] == 2
    summary_view = db_client.get(f"/assistant/artifacts/{summary.id}", headers=headers).json()
    assert summary_view["is_latest"] and not summary_view["is_final_result"]
    assert summary_view["stream_key"] == "summary"
    first_hash = draft_review.payload_hash(draft)
    async with db_sessionmaker.begin() as session:
        await draft_review.review(
            session,
            owner,
            draft.id,
            ReviewDraftRequest(expected_revision=1, payload_hash=first_hash),
        )
        _, edited = await draft_review.edit(
            session, owner, tid, edit_request(subject=draft.payload["content"]["subject"])
        )
    result = db_client.get(f"/assistant/tasks/{tid}/draft-revisions", headers=headers).json()
    assert [a["revision"] for a in result["revisions"]] == [2, 1]
    assert all(a["artifact_id"] != summary.id for a in result["revisions"])
    assert (
        db_client.get(f"/assistant/tasks/{tid}", headers=headers).json()["artifact_id"] == edited.id
    )
    assert (
        db_client.get(f"/assistant/artifacts/{draft.id}", headers=headers).json()["review"]["state"]
        == "stale"
    )
    async with db_sessionmaker() as session:
        assert await session.get(DraftReview, draft.id)
        assert (
            draft_review.payload_hash(await session.get(ArtifactRevision, draft.id)) == first_hash
        )
        assert (await session.get(ArtifactRevision, summary.id)).payload == summary.payload


@needs_pg
async def test_retry_reuses_summary_and_preserves_partial_state(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    model = PairModel(fail_draft=True)
    await run_once(db_sessionmaker, model)
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "queued" and task.final_artifact_id is None
    assert len(artifacts) == 1 and parts[0].state == "succeeded" and parts[1].state == "failed"
    summary_id = artifacts[0].id
    headers = auth_headers(mailbox[0])
    partial = db_client.get(f"/assistant/tasks/{tid}", headers=headers).json()
    assert partial["compound"]["completed_steps"] == 1 and partial["artifact_id"] is None
    assert db_client.get(f"/assistant/artifacts/{summary_id}", headers=headers).status_code == 200
    await make_due(db_sessionmaker, tid)
    recovery = PairModel()
    await run_once(db_sessionmaker, recovery)
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "succeeded" and len(artifacts) == 2
    assert len(recovery.calls) == 1 and "DRAFT_REQUEST_JSON" in recovery.calls[0]
    assert parts[0].artifact_id == summary_id and [s.attempts for s in parts] == [1, 2]
    async with db_sessionmaker() as session:
        events = (await session.scalars(select(TaskEvent).where(TaskEvent.task_id == tid))).all()
        assert sum(e.kind == "artifact.ready" for e in events) == 1
        assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps([e.payload for e in events])


@pytest.mark.parametrize("stage", ["summary", "draft"])
@needs_pg
async def test_invalid_sources_stop_without_publishing_that_step(db_sessionmaker, mailbox, stage):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    await run_once(
        db_sessionmaker,
        PairModel(invalid_summary=stage == "summary", invalid_draft=stage == "draft"),
    )
    task, artifacts, _ = await saved(db_sessionmaker, tid)
    assert task.state == "failed" and task.final_artifact_id is None
    assert len(artifacts) == (0 if stage == "summary" else 1)
    assert task.error_code == f"invalid_{stage}_output"


@needs_pg
async def test_changed_source_after_partial_result_never_generates_dependent_draft(
    db_sessionmaker, mailbox
):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, PairModel(fail_draft=True))
    _, artifacts, _ = await saved(db_sessionmaker, tid)
    original = copy.deepcopy(artifacts[0].payload)
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=Thread.version + 1))
    await make_due(db_sessionmaker, tid)
    model = PairModel()
    await run_once(db_sessionmaker, model)
    task, artifacts, _ = await saved(db_sessionmaker, tid)
    assert task.state == "failed" and task.error_code == "read_source_changed"
    assert model.calls == [] and len(artifacts) == 1 and artifacts[0].payload == original


@pytest.mark.parametrize(
    "stage,interruption",
    [(1, "cancel"), (2, "cancel"), (1, "replace"), (2, "replace"), (2, "delete")],
)
@needs_pg
async def test_inflight_publication_is_fenced(db_sessionmaker, mailbox, stage, interruption):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    started, release = asyncio.Event(), asyncio.Event()

    class Blocked(PairModel):
        async def generate(self, prompt, **kwargs):
            result = await super().generate(prompt, **kwargs)
            if len(self.calls) == stage:
                started.set()
                await release.wait()
            return result

    worker = asyncio.create_task(run_once(db_sessionmaker, Blocked()))
    await asyncio.wait_for(started.wait(), 5)
    try:
        async with db_sessionmaker.begin() as session:
            task = await session.get(AssistantTask, tid)
            if interruption == "cancel":
                await tasks.cancel(session, mailbox[0], tid, task.version)
            elif interruption == "delete":
                await session.execute(delete(AssistantTask).where(AssistantTask.id == tid))
            else:
                await session.execute(
                    update(AssistantJob)
                    .where(AssistantJob.task_id == tid)
                    .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
        if interruption == "replace":
            assert await run_once(db_sessionmaker, PairModel())
    finally:
        release.set()
        await worker
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    if interruption == "delete":
        assert task is None and artifacts == []
    elif interruption == "replace":
        assert task.state == "succeeded" and len(artifacts) == 2
    else:
        assert task.state == "cancelled" and len(artifacts) == stage - 1
        assert parts[-1].state == "cancelled"


@needs_pg
async def test_stale_claim_cannot_checkpoint_or_take_another_owners_source(
    db_sessionmaker, mailbox
):
    tid, request = await submit(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        claim = await tasks.claim_next(session)
    assert await steps.start_step(
        db_sessionmaker, replace(claim, user_id=mailbox[1]), request, 1, None
    ) == (False, None)
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(AssistantJob)
            .where(AssistantJob.task_id == tid)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await steps.start_step(db_sessionmaker, claim, request, 1, None) == (False, None)


@needs_pg
async def test_owner_scope_idempotency_and_whole_template_preflight(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    tid, request = await submit(db_sessionmaker, mailbox[0])
    for path in [f"/assistant/tasks/{tid}", f"/assistant/tasks/{tid}/events"]:
        assert db_client.get(path, headers=auth_headers(mailbox[1])).status_code == 404
    payload = request.model_dump()
    assert (
        db_client.post(
            "/assistant/compound-requests", json=payload, headers=auth_headers(mailbox[1])
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            "/assistant/compound-requests",
            json={**payload, "summary_in_draft": False},
            headers=auth_headers(mailbox[0]),
        ).status_code
        == 409
    )
    for extra in [
        {"template": "summary_slots_reply"},
        {"operations": ["suggest_slots"]},
        {"requested_action": "send_email"},
        {"steps": [{"depends_on": ["self"]}]},
        {"flow_arn": "arbitrary"},
    ]:
        result = db_client.post(
            "/assistant/compound-requests",
            json={**payload, "request_id": "invalid", **extra},
            headers=auth_headers(mailbox[0]),
        )
        assert result.status_code == 422, result.text
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 1
        assert await session.scalar(select(func.count()).select_from(AssistantStep)) == 0
    await run_once(db_sessionmaker, PairModel())
    _, artifacts, _ = await saved(db_sessionmaker, tid)
    for artifact in artifacts:
        assert (
            db_client.get(
                f"/assistant/artifacts/{artifact.id}", headers=auth_headers(mailbox[1])
            ).status_code
            == 404
        )


@needs_pg
async def test_final_and_step_refs_cannot_cross_tasks_even_for_same_owner(db_sessionmaker, mailbox):
    first, _ = await submit(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, PairModel())
    second, _ = await submit(db_sessionmaker, mailbox[0], key="second")
    await run_once(db_sessionmaker, PairModel())
    _, artifacts, _ = await saved(db_sessionmaker, first)
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(AssistantTask)
                .where(AssistantTask.id == second)
                .values(final_artifact_id=artifacts[0].id)
            )
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(AssistantStep)
                .where(AssistantStep.task_id == second, AssistantStep.ordinal == 1)
                .values(artifact_id=artifacts[0].id)
            )


@needs_pg
async def test_release_mismatch_runs_zero_steps(db_sessionmaker, mailbox):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, tid)
        task.release = {**task.release, "contract_hash": "0" * 64}
    model = PairModel()
    await run_once(db_sessionmaker, model)
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "failed" and task.error_code == "release_unavailable"
    assert artifacts == parts == model.calls == []


@needs_pg
async def test_retry_exhaustion_preserves_one_summary(db_sessionmaker, mailbox):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    model = PairModel(fail_draft=True)
    for _ in range(3):
        await make_due(db_sessionmaker, tid)
        await run_once(db_sessionmaker, model)
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "failed" and len(artifacts) == 1
    assert [s.attempts for s in parts] == [1, 3] and len(model.calls) == 4


def test_incomplete_or_conflicting_templates_rejected():
    for change in [
        {"draft_options": {"to": []}},
        {"template": "summary_then_reply"},
        {"summary_in_draft": "yes"},
        {"context_snapshot_id": ""},
    ]:
        with pytest.raises(ValidationError):
            compound("source", **change)


@needs_pg
async def test_duplicate_acceptance_has_one_job(db_sessionmaker, mailbox):
    context = await capture(db_sessionmaker, mailbox[0])
    request = compound(context)

    async def accept():
        async with db_sessionmaker.begin() as session:
            return (
                await tasks.submit(session, mailbox[0], request.as_request(), compound=request)
            ).id

    ids = await asyncio.gather(accept(), accept())
    assert ids[0] == ids[1]
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 1
        assert await session.scalar(select(func.count()).select_from(TaskEvent)) == 1


@needs_pg
async def test_task_delete_cascades_streams_and_reviews(db_sessionmaker, mailbox):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, PairModel())
    task, artifacts, _ = await saved(db_sessionmaker, tid)
    draft = next(a for a in artifacts if a.id == task.final_artifact_id)
    async with db_sessionmaker.begin() as session:
        await draft_review.review(
            session,
            mailbox[0],
            draft.id,
            ReviewDraftRequest(expected_revision=1, payload_hash=draft_review.payload_hash(draft)),
        )
    async with db_sessionmaker.begin() as session:
        await session.execute(delete(AssistantTask).where(AssistantTask.id == tid))
    async with db_sessionmaker() as session:
        for table in (AssistantStep, ArtifactRevision, DraftReview):
            assert await session.scalar(select(func.count()).select_from(table)) == 0


@needs_pg
async def test_saved_summary_cannot_be_tampered_with_before_retry(db_sessionmaker, mailbox):
    tid, _ = await submit(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, PairModel(fail_draft=True))
    async with db_sessionmaker.begin() as session:
        artifact = await session.scalar(
            select(ArtifactRevision).where(ArtifactRevision.task_id == tid)
        )
        artifact.payload = {**artifact.payload, "coverage": "tampered"}
    await make_due(db_sessionmaker, tid)
    model = PairModel()
    await run_once(db_sessionmaker, model)
    task, artifacts, _ = await saved(db_sessionmaker, tid)
    assert task.error_code == "compound_checkpoint_invalid" and task.state == "failed"
    assert model.calls == [] and len(artifacts) == 1


@needs_pg
async def test_compound_uses_saved_flow_targets_and_real_invoker_contract(
    db_sessionmaker, mailbox, monkeypatch
):
    from app.config import get_settings
    from tests.test_workflow_runtime import FakeSdk, Stream, complete, manifest, output

    monkeypatch.setenv("INFERENCE_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "synthetic-router")
    monkeypatch.setenv("ASSISTANT_WORKFLOW_MANIFEST", json.dumps(manifest()))
    get_settings.cache_clear()
    try:
        tid, _ = await submit(db_sessionmaker, mailbox[0])
        # Change only dispatch config after acceptance: queued steps must retain their targets.
        monkeypatch.setenv(
            "ASSISTANT_WORKFLOW_MANIFEST", json.dumps(manifest({"implementation": "native"}))
        )
        get_settings.cache_clear()

        class PairSdk(FakeSdk):
            def invoke_flow(self, **kwargs):
                document = kwargs["inputs"][0]["content"]["document"]
                value = DRAFT if "DRAFT_REQUEST_JSON" in document else SUMMARY
                self.stream = Stream([output(json.dumps(value)), complete()])
                return super().invoke_flow(**kwargs)

        sdk, model = PairSdk(), PairModel()
        await run_once(db_sessionmaker, model, sdk.invoker())
        task, artifacts, parts = await saved(db_sessionmaker, tid)
        assert task.state == "succeeded" and len(artifacts) == len(sdk.invocations) == 2
        assert model.calls == []
        assert all(s.release == task.release for s in parts)
        assert all(a.provenance["provider"] == "bedrock_flow" for a in artifacts)
    finally:
        get_settings.cache_clear()


@needs_pg
async def test_repeated_worker_crashes_exhaust_budget_without_running_or_success_state(
    db_sessionmaker, mailbox
):
    tid, request = await submit(db_sessionmaker, mailbox[0])
    for _ in range(3):
        async with db_sessionmaker.begin() as session:
            claim = await tasks.claim_next(session)
        active, checkpoint = await steps.start_step(db_sessionmaker, claim, request, 1, None)
        assert active and checkpoint is None
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(AssistantJob)
                .where(AssistantJob.task_id == tid)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    async with db_sessionmaker.begin() as session:
        assert await tasks.claim_next(session) is None
    task, artifacts, parts = await saved(db_sessionmaker, tid)
    assert task.state == "failed" and task.error_code == "attempts_exhausted"
    assert parts[0].state == "failed" and parts[0].attempts == 3 and artifacts == []
