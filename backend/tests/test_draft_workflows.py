"""Draft artifact acceptance and envelope boundaries; model outputs are synthetic."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.api.errors import ApiError
from app.assistant import routing_v1, tasks
from app.assistant.context import capture_thread
from app.assistant.summary import digest
from app.assistant.worker import run_once
from app.db.models import ArtifactRevision, AssistantJob, AssistantTask, Message, Thread
from app.model_client.client import GenResult
from app.model_client.providers import ProviderError
from app.schemas.assistant import DraftOptions
from tests.conftest import needs_pg
from tests.test_contextual_routing import PipelineModel
from tests.test_durable_tasks import counts, create_task, mailbox, request
from tests.test_intent_router import proposal

__all__ = ["mailbox"]
OUTPUT = {
    "subject": "Project update",
    "body": "Hello, here is the update.",
    "unresolved_fields": [],
    "sources": [],
}


class DraftModel:
    def __init__(self, *, reply=False, output=None, fail=False):
        self.output = (
            ({**OUTPUT, "subject": "Re: Release", "sources": [1]} if reply else OUTPUT)
            if output is None
            else output
        )
        self.fail = fail
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.fail:
            raise ProviderError("private failure")
        return json.dumps(self.output), GenResult("fake", "draft-model")


async def draft_task(factory, owner, *, reply=False, options=None, key="draft", context_id=None):
    if reply and context_id is None:
        async with factory.begin() as session:
            await session.execute(
                update(Message).values(
                    subject="Release",
                    reply_metadata={"headers": {"message-id": ["<message@example.test>"]}},
                )
            )
            context_id = (await capture_thread(session, owner, "thread-one")).id
    if options is None:
        options = {
            "to": ["recipient@example.test"],
            "cc": [],
            "bcc": ["private@example.test"],
            "reply_message_id": "m1" if reply else None,
        }
    async with factory.begin() as session:
        value = request(
            context_id,
            key=key,
            instruction="Draft a reply" if reply else "Write an email",
            intent_hint="reply" if reply else "compose",
            draft_options=options,
        )
        task = await tasks.submit(session, owner, value)
        return task.id, value


async def artifact_for(factory, tid):
    async with factory() as session:
        return (
            await session.execute(select(ArtifactRevision).where(ArtifactRevision.task_id == tid))
        ).scalar_one()


@pytest.mark.parametrize(
    "address",
    [
        "Name <person@example.test>",
        "victim@example.test\r\nBcc: attacker@example.test",
        "bad address@example.test",
        "a@@example.test",
        "",
        "a..b@example.test",
        ".a@example.test",
    ],
)
def test_recipient_literals_reject_names_and_injection(address):
    with pytest.raises(ValidationError):
        DraftOptions(to=[address])


def test_recipient_normalization_and_cross_field_deduplication():
    assert DraftOptions(to=["Person@Example.Test"]).to == ["person@example.test"]
    with pytest.raises(ValidationError):
        DraftOptions(to=["PERSON@example.test"], bcc=["person@example.test"])
    with pytest.raises(ValidationError):
        DraftOptions(to=[f"p{i}@example.test" for i in range(20)], cc=["extra@example.test"])


@needs_pg
async def test_compose_artifact_has_explicit_envelope_and_no_inherited_thread(
    db_sessionmaker,
    mailbox,
    db_client,
    auth_headers,
):
    tid, value = await draft_task(db_sessionmaker, mailbox[0])
    model = DraftModel()
    await run_once(db_sessionmaker, model)
    assert len(model.calls) == 1
    assert "recipient@example.test" not in model.calls[0][0]
    assert "private@example.test" not in model.calls[0][0]
    view = db_client.get(f"/assistant/tasks/{tid}", headers=auth_headers(mailbox[0])).json()
    assert view["state"] == "succeeded" and view["intent"] == "compose"
    response = db_client.get(
        f"/assistant/artifacts/{view['artifact_id']}", headers=auth_headers(mailbox[0])
    ).json()
    artifact = response["artifact"]
    assert not response["sending_available"]
    assert response["draft_envelope"]["to"] == value.draft_options.to
    assert response["draft_envelope"]["bcc"] == ["private@example.test"]
    assert response["draft_envelope"]["from_address"] == "first@example.test"
    assert response["draft_envelope"]["reply"] is None
    assert artifact["kind"] == "draft" and artifact["context_snapshot_id"] is None
    assert artifact["content"]["thread_ref"] is None and artifact["content"]["bcc_refs"] == [
        "bcc-1"
    ]
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/implementation-playbook/schemas/artifact.schema.json"
        ).read_text()
    )
    jsonschema.validate(artifact, schema)
    assert (
        db_client.get(
            f"/assistant/artifacts/{view['artifact_id']}", headers=auth_headers(mailbox[1])
        ).status_code
        == 404
    )
    assert db_client.post("/draft/1/send", headers=auth_headers(mailbox[0])).status_code == 501


@needs_pg
async def test_reply_binds_selected_message_subject_and_pinned_source(db_sessionmaker, mailbox):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0], reply=True)
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Message).values(body_clean="Changed after acceptance"))
        await session.execute(update(Thread).values(version=Thread.version + 1))
    model = DraftModel(reply=True)
    await run_once(db_sessionmaker, model)
    assert (
        "Ship Friday." in model.calls[0][0] and "Changed after acceptance" not in model.calls[0][0]
    )
    assert '"reply_target": true' in model.calls[0][0]
    assert "<message@example.test>" not in model.calls[0][0]
    artifact = await artifact_for(db_sessionmaker, tid)
    assert artifact.payload["content"]["subject"] == "Re: Release"
    assert artifact.payload["content"]["thread_ref"] == "thread-one"
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, tid)
        assert task.draft_input["reply"]["gmail_message_id"] == "m1"
        assert task.draft_input["reply"]["thread_version"] == 0
        assert task.draft_input["reply"]["rfc_message_id"] == "<message@example.test>"


@needs_pg
async def test_stale_or_wrong_snapshot_target_rejected_before_task_creation(
    db_sessionmaker, mailbox
):
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
        cid = context.id
    with pytest.raises(ApiError) as wrong:
        await draft_task(
            db_sessionmaker,
            mailbox[0],
            reply=True,
            context_id=cid,
            options={"to": ["p@example.test"], "reply_message_id": "outside-snapshot"},
        )
    assert wrong.value.code == "reply_target_not_found"
    with pytest.raises(ApiError) as owner:
        await draft_task(db_sessionmaker, mailbox[1], reply=True, context_id=cid)
    assert owner.value.code == "context_not_found"
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=1))
    with pytest.raises(ApiError) as stale:
        await draft_task(db_sessionmaker, mailbox[0], reply=True, context_id=cid)
    assert stale.value.code == "reply_context_changed"
    assert await counts(db_sessionmaker) == [0, 0, 0, 0]


@needs_pg
async def test_recipient_edit_changes_request_hash_and_never_reuses_wrong_envelope(
    db_sessionmaker, mailbox
):
    tid, value = await draft_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        assert (await tasks.submit(session, mailbox[0], value)).id == tid
    changed = value.model_copy(update={"draft_options": DraftOptions(to=["other@example.test"])})
    async with db_sessionmaker.begin() as session:
        with pytest.raises(ApiError) as conflict:
            await tasks.submit(session, mailbox[0], changed)
        assert conflict.value.code == "idempotency_conflict"
    assert await counts(db_sessionmaker) == [1, 1, 1, 0]


@needs_pg
@pytest.mark.parametrize(
    "output",
    [
        {**OUTPUT, "to": ["attacker@example.test"]},
        {**OUTPUT, "subject": "Hi\r\nBcc: attacker@example.test"},
        {**OUTPUT, "sources": [1]},  # context-free compose has no message sources
        {**OUTPUT, "sources": [True]},
        {**OUTPUT, "attachment_refs": ["invented-file"]},
        {**OUTPUT, "unresolved_fields": ["x" * 301]},
    ],
)
async def test_invalid_model_output_cannot_change_envelope_or_publish(
    db_sessionmaker, mailbox, output
):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, DraftModel(output=output))
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, tid)
        assert task.state == "failed" and task.error_code == "invalid_draft_output"
        assert task.draft_input["to"] == ["recipient@example.test"]
    assert (await counts(db_sessionmaker))[-1] == 0


@needs_pg
async def test_model_cannot_change_reply_subject(db_sessionmaker, mailbox):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0], reply=True)
    await run_once(db_sessionmaker, DraftModel(output=OUTPUT))
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, tid)).state == "succeeded"
    artifact = await artifact_for(db_sessionmaker, tid)
    assert artifact.payload["content"]["subject"] == "Re: Release"


@needs_pg
async def test_missing_facts_and_placeholders_remain_visible(db_sessionmaker, mailbox):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0])
    await run_once(
        db_sessionmaker,
        DraftModel(
            output={
                **OUTPUT,
                "body": "Please send [document].",
                "unresolved_fields": ["Document not supplied"],
            }
        ),
    )
    draft = (await artifact_for(db_sessionmaker, tid)).payload["content"]
    assert "Document not supplied" in draft["unresolved_fields"]
    assert any("placeholder" in field for field in draft["unresolved_fields"])
    assert draft["attachment_refs"] == []


@needs_pg
async def test_draft_retry_reuses_envelope_and_route(db_sessionmaker, mailbox):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, DraftModel(fail=True))
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, tid)
        assert task.state == "queued" and task.route["decision"]["intent"] == "compose"
        (await session.get(AssistantJob, tid)).available_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    await run_once(db_sessionmaker, DraftModel())
    assert (await counts(db_sessionmaker))[-1] == 1


@needs_pg
async def test_cancel_draft_generation_prevents_artifact(db_sessionmaker, mailbox):
    tid, _ = await draft_task(db_sessionmaker, mailbox[0])
    started, release = asyncio.Event(), asyncio.Event()

    class Paused(DraftModel):
        async def generate(self, *args, **kwargs):
            started.set()
            await release.wait()
            return await super().generate(*args, **kwargs)

    pending = asyncio.create_task(run_once(db_sessionmaker, Paused()))
    await asyncio.wait_for(started.wait(), 5)
    try:
        async with db_sessionmaker.begin() as session:
            await asyncio.wait_for(tasks.cancel(session, mailbox[0], tid, 3), 3)
    finally:
        release.set()
        await pending
    assert (await counts(db_sessionmaker))[-1] == 0


@needs_pg
async def test_previous_contextual_release_keeps_original_behavior_and_hash(
    db_sessionmaker, mailbox
):
    tid, cid = await create_task(db_sessionmaker, mailbox[0])
    original_request = request(cid)
    # Reconstruct the actual historical payload, before optional action fields existed.
    old_value = original_request.model_dump(exclude={"draft_options", "read_options"})
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, tid)
        task.release = routing_v1.release_manifest()
        task.request_hash = digest(old_value)
    async with db_sessionmaker.begin() as session:
        assert (await tasks.submit(session, mailbox[0], original_request)).id == tid
    model = PipelineModel()
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, tid)
        assert task.state == "succeeded" and task.route["release"] == "contextual-task-1.0.0"
    assert len(model.calls) == 1


@needs_pg
async def test_calendar_dependent_draft_never_runs_plain_draft(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        task = await tasks.submit(
            session,
            mailbox[0],
            request(
                None,
                instruction="Reply with three slots",
                intent_hint=None,
                draft_options={"to": ["recipient@example.test"]},
            ),
        )
        tid = task.id
    model = PipelineModel(
        proposal(
            intent="plan_schedule", output_kind="draft", operations=["suggest_slots", "draft_reply"]
        )
    )
    await run_once(db_sessionmaker, model)
    assert len(model.calls) == 1 and model.calls[0][1]["small"]
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, tid)).state == "needs_clarification"
    assert (await counts(db_sessionmaker))[-1] == 0


@needs_pg
async def test_compose_with_background_thread_stays_new_mail(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        cid = (await capture_thread(session, mailbox[0], "thread-one")).id
    tid, _ = await draft_task(db_sessionmaker, mailbox[0], context_id=cid)
    await run_once(db_sessionmaker, DraftModel(output={**OUTPUT, "sources": [1]}))
    artifact = (await artifact_for(db_sessionmaker, tid)).payload
    assert artifact["content"]["mode"] == "new" and artifact["content"]["thread_ref"] is None
    assert artifact["content"]["fact_ref_ids"] == ["user-request", "source-1"]


@needs_pg
async def test_reply_needs_explicit_recipients_even_with_bound_target(db_sessionmaker, mailbox):
    tid, _ = await draft_task(
        db_sessionmaker, mailbox[0], reply=True, options={"reply_message_id": "m1"}
    )
    model = DraftModel(reply=True)
    await run_once(db_sessionmaker, model)
    assert model.calls == []
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, tid)
        assert task.state == "needs_clarification"
        assert task.route["decision"]["missing_fields"] == ["recipient"]


@pytest.mark.parametrize(
    "address", ["a@host.-invalid", "a@host.invalid-", "x" * 65 + "@example.test"]
)
def test_recipient_label_and_local_part_bounds(address):
    with pytest.raises(ValidationError):
        DraftOptions(to=[address])


def test_generated_text_rejects_database_control_characters():
    from app.assistant.drafting import GeneratedDraft

    with pytest.raises(ValidationError):
        GeneratedDraft(**{**OUTPUT, "body": "Hello\x00world"})
