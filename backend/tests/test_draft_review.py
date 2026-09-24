"""Review/edit races and exact-revision persistence against real PostgreSQL."""

import asyncio
import copy
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

from app.api.errors import ApiError
from app.assistant import draft_review
from app.assistant.worker import run_once
from app.db.models import (
    ArtifactRevision,
    AssistantTask,
    DraftReview,
    Message,
    TaskEvent,
    Thread,
    User,
)
from app.schemas.draft_review import EditDraftRequest, ReviewDraftRequest
from tests.conftest import needs_pg
from tests.test_draft_workflows import DraftModel, artifact_for, draft_task
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]


def edit_request(**changes):
    return EditDraftRequest(
        **{
            "request_id": "edit-1",
            "expected_revision": 1,
            "subject": "Updated project",
            "body": "Please send the report on Monday.",
            "recipients": {"to": ["NEW@example.test"], "cc": [], "bcc": ["secret@example.test"]},
            "unresolved_fields": [],
            **changes,
        }
    )


async def generated(factory, owner, reply=False, output=None):
    tid, _ = await draft_task(factory, owner, reply=reply)
    await run_once(factory, DraftModel(reply=reply, output=output))
    return await artifact_for(factory, tid)


async def save(factory, owner, tid, request=None):
    async with factory.begin() as session:
        _, artifact = await draft_review.edit(session, owner, tid, request or edit_request())
        return artifact


async def acknowledge(factory, owner, artifact):
    async with factory.begin() as session:
        return await draft_review.review(
            session,
            owner,
            artifact.id,
            ReviewDraftRequest(
                expected_revision=artifact.revision,
                payload_hash=draft_review.payload_hash(artifact),
            ),
        )


@needs_pg
async def test_revision_api_preserves_original_and_exposes_latest(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    first = await generated(db_sessionmaker, mailbox[0])
    original = copy.deepcopy(first.payload)
    headers = auth_headers(mailbox[0])
    response = db_client.post(
        f"/assistant/tasks/{first.task_id}/draft-revisions",
        json=edit_request().model_dump(),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["revision"] == 2 and value["is_latest"]
    assert value["draft_envelope"]["to"] == ["new@example.test"]
    assert value["draft_envelope"]["bcc"] == ["secret@example.test"]
    assert value["review"]["state"] == "unreviewed" and not value["sending_available"]
    assert value["artifact"]["content"]["fact_ref_ids"] == ["user-edit"]
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/implementation-playbook/schemas/artifact.schema.json"
        ).read_text()
    )
    jsonschema.validate(value["artifact"], schema)
    old = db_client.get(f"/assistant/artifacts/{first.id}", headers=headers).json()
    assert old["artifact"] == original and old["revision"] == 1 and not old["is_latest"]
    assert old["draft_envelope"]["to"] == ["recipient@example.test"]
    task = db_client.get(f"/assistant/tasks/{first.task_id}", headers=headers).json()
    assert task["artifact_id"] == value["artifact_id"] and task["state"] == "succeeded"
    assert task["draft_input"]["to"] == ["recipient@example.test"]
    page = db_client.get(
        f"/assistant/tasks/{first.task_id}/draft-revisions?page_size=1", headers=headers
    ).json()
    assert page["revisions"][0]["revision"] == 2 and page["next_before_revision"] == 2
    page = db_client.get(
        f"/assistant/tasks/{first.task_id}/draft-revisions?before_revision=2", headers=headers
    ).json()
    assert [r["revision"] for r in page["revisions"]] == [1]
    assert page["next_before_revision"] is None


@needs_pg
async def test_ai_revision_is_unverified_instead_of_inheriting_source_evidence(
    db_sessionmaker, mailbox
):
    first = await generated(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        _, revised = await draft_review.edit(
            session,
            mailbox[0],
            first.task_id,
            edit_request(),
            author="conversation_model",
            author_provenance={
                "source": "conversation_user_turns",
                "authority": "user_dialogue_only",
                "conversation_id": "conversation-test",
                "turn_request_id": "turn-test",
                "instruction_hash": "i" * 64,
                "provider": "bedrock",
                "model_id": "test-profile",
                "release": "contextual-conversation-test",
                "prompt_hash": "p" * 64,
                "tools_hash": "t" * 64,
            },
        )
    assert revised.payload["content"]["fact_ref_ids"] == ["ai-revision"]
    assert [e["ref_id"] for e in revised.payload["evidence"]] == ["ai-revision"]
    assert "not revalidated" in revised.payload["assumptions"][0]
    assert revised.provenance["conversation"]["prompt_hash"] == "p" * 64
    assert revised.provenance["conversation"]["tools_hash"] == "t" * 64


@needs_pg
async def test_exact_review_is_idempotent_and_edit_invalidates_it(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    first = await generated(db_sessionmaker, mailbox[0])
    headers = auth_headers(mailbox[0])
    url = f"/assistant/artifacts/{first.id}/review"
    payload = {"expected_revision": 1, "payload_hash": draft_review.payload_hash(first)}
    bad = db_client.post(url, json={**payload, "payload_hash": "0" * 64}, headers=headers)
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "review_payload_changed"
    for _ in range(2):
        value = db_client.post(url, json=payload, headers=headers)
        assert value.status_code == 200, value.text
        assert value.json()["review"]["state"] == "reviewed"
        assert value.json()["review"]["authorization"] == "none"
        assert not value.json()["sending_available"]
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(DraftReview)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TaskEvent)
                .where(TaskEvent.kind == "draft.reviewed")
            )
            == 1
        )
    second = await save(db_sessionmaker, mailbox[0], first.task_id)
    assert draft_review.payload_hash(first) != draft_review.payload_hash(second)
    assert db_client.post(url, json=payload, headers=headers).status_code == 409
    old = db_client.get(f"/assistant/artifacts/{first.id}", headers=headers).json()
    assert old["review"]["state"] == "stale"
    assert "revision_superseded" in old["review"]["blockers"]
    new = db_client.get(f"/assistant/artifacts/{second.id}", headers=headers).json()
    assert new["review"]["state"] == "unreviewed"
    await acknowledge(db_sessionmaker, mailbox[0], second)


@needs_pg
async def test_concurrent_edits_serialize_and_replays_do_not_duplicate(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0])
    results = await asyncio.gather(
        save(db_sessionmaker, mailbox[0], first.task_id),
        save(db_sessionmaker, mailbox[0], first.task_id),
    )
    assert results[0].id == results[1].id
    with pytest.raises(ApiError) as error:
        await save(db_sessionmaker, mailbox[0], first.task_id, edit_request(body="Different"))
    assert error.value.code == "idempotency_conflict"
    results = await asyncio.gather(
        *[
            save(
                db_sessionmaker,
                mailbox[0],
                first.task_id,
                edit_request(expected_revision=2, request_id=f"competing-{i}"),
            )
            for i in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, ArtifactRevision) for r in results) == 1
    assert [r.code for r in results if isinstance(r, ApiError)] == ["revision_conflict"]
    # Lost-response replay returns the original revision even after newer edits exist.
    replay = await save(db_sessionmaker, mailbox[0], first.task_id)
    assert replay.revision == 2
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 3
        assert (
            await session.scalar(
                select(func.count()).select_from(TaskEvent).where(TaskEvent.kind == "draft.revised")
            )
            == 2
        )


@needs_pg
async def test_edit_review_race_never_reviews_changed_payload(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0])
    result = await asyncio.gather(
        acknowledge(db_sessionmaker, mailbox[0], first),
        save(db_sessionmaker, mailbox[0], first.task_id),
        return_exceptions=True,
    )
    assert isinstance(result[1], ArtifactRevision)
    if isinstance(result[0], ApiError):
        assert result[0].code == "revision_conflict"
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, first.task_id)
        value = await draft_review.artifact_view(session, task, result[1])
        assert value["review"]["state"] == "unreviewed"
        assert await session.get(DraftReview, result[1].id) is None


@needs_pg
async def test_cross_owner_mutations_and_reads_rejected(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    first = await generated(db_sessionmaker, mailbox[0])
    headers = auth_headers(mailbox[1])
    for path in (
        f"/assistant/artifacts/{first.id}",
        f"/assistant/tasks/{first.task_id}/draft-revisions",
    ):
        assert db_client.get(path, headers=headers).status_code == 404
    assert (
        db_client.post(
            f"/assistant/tasks/{first.task_id}/draft-revisions",
            json=edit_request().model_dump(),
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/assistant/artifacts/{first.id}/review",
            json={"expected_revision": 1, "payload_hash": draft_review.payload_hash(first)},
            headers=headers,
        ).status_code
        == 404
    )


@needs_pg
@pytest.mark.parametrize("change", ["source", "message_deleted", "sender"])
async def test_changed_source_or_sender_makes_review_stale(db_sessionmaker, mailbox, change):
    first = await generated(db_sessionmaker, mailbox[0], reply=True)
    await acknowledge(db_sessionmaker, mailbox[0], first)
    async with db_sessionmaker.begin() as session:
        if change == "source":
            await session.execute(update(Thread).values(version=Thread.version + 1))
        elif change == "message_deleted":
            await session.execute(delete(Message).where(Message.gmail_msg_id == "m1"))
        else:
            await session.execute(
                update(User).where(User.id == mailbox[0]).values(email="x@example.test")
            )
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, first.task_id)
        view = await draft_review.artifact_view(session, task, first)
        assert view["review"]["state"] == "stale" and view["review"]["blockers"]
    with pytest.raises(ApiError) as error:
        await acknowledge(db_sessionmaker, mailbox[0], first)
    assert error.value.code == "draft_review_blocked"


@needs_pg
@pytest.mark.parametrize(
    "changes",
    [
        {"body": "Hello [name]"},
        {"subject": "About {{project}}"},
        {"unresolved_fields": ["Confirm the promised deadline"]},
    ],
)
async def test_missing_facts_block_review_until_explicitly_edited(
    db_sessionmaker, mailbox, changes
):
    first = await generated(db_sessionmaker, mailbox[0])
    second = await save(db_sessionmaker, mailbox[0], first.task_id, edit_request(**changes))
    with pytest.raises(ApiError) as error:
        await acknowledge(db_sessionmaker, mailbox[0], second)
    assert error.value.code == "draft_review_blocked"
    third = await save(
        db_sessionmaker,
        mailbox[0],
        first.task_id,
        edit_request(expected_revision=2, request_id="resolved"),
    )
    await acknowledge(db_sessionmaker, mailbox[0], third)


@needs_pg
async def test_reply_identity_fixed_and_missing_header_cannot_be_cleared(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0], reply=True)
    with pytest.raises(ApiError) as error:
        await save(db_sessionmaker, mailbox[0], first.task_id)
    assert error.value.code == "reply_subject_fixed"
    second = await save(
        db_sessionmaker,
        mailbox[0],
        first.task_id,
        edit_request(subject=first.payload["content"]["subject"]),
    )
    assert second.draft_envelope["reply"] == first.draft_envelope["reply"]
    # Simulate an initial artifact bound without original Message-ID.
    async with db_sessionmaker.begin() as session:
        env = copy.deepcopy(second.draft_envelope)
        env["reply"]["rfc_message_id"] = None
        await session.execute(
            update(ArtifactRevision)
            .where(ArtifactRevision.id == second.id)
            .values(draft_envelope=env)
        )
    third = await save(
        db_sessionmaker,
        mailbox[0],
        first.task_id,
        edit_request(
            subject=first.payload["content"]["subject"],
            expected_revision=2,
            request_id="clear-warning",
        ),
    )
    with pytest.raises(ApiError) as error:
        await acknowledge(db_sessionmaker, mailbox[0], third)
    assert error.value.code == "draft_review_blocked"


@needs_pg
async def test_account_deletion_cascades_revisions_and_reviews(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0])
    second = await save(db_sessionmaker, mailbox[0], first.task_id)
    await acknowledge(db_sessionmaker, mailbox[0], second)
    async with db_sessionmaker.begin() as session:
        await session.execute(delete(User).where(User.id == mailbox[0]))
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 0
        assert await session.scalar(select(func.count()).select_from(DraftReview)) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "x\r\nBcc: a@b.test"},
        {"subject": " "},
        {"body": "\x00"},
        {"body": " "},
        {"unresolved_fields": [" "]},
        {"unresolved_fields": ["a" * 301]},
        {"recipients": {"to": ["a@b.test"], "bcc": ["A@b.test"]}},
        {"recipients": {"to": ["a@b.test"], "reply_message_id": "injected"}},
        {"from_address": "attacker@example.test"},
        {"attachment_refs": ["invented"]},
        {"expected_revision": True},
    ],
)
def test_strict_edit_boundaries(changes):
    with pytest.raises(ValidationError):
        edit_request(**changes)


@needs_pg
async def test_review_duplicates_serialize_and_events_exclude_content(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0])
    await asyncio.gather(*[acknowledge(db_sessionmaker, mailbox[0], first) for _ in range(2)])
    await save(db_sessionmaker, mailbox[0], first.task_id)
    async with db_sessionmaker() as session:
        events = (
            await session.scalars(
                select(TaskEvent).where(TaskEvent.kind.in_(["draft.reviewed", "draft.revised"]))
            )
        ).all()
        assert len(events) == 2
        assert "example.test" not in json.dumps([e.payload for e in events])
        assert "Monday" not in json.dumps([e.payload for e in events])
        assert await session.scalar(select(func.count()).select_from(DraftReview)) == 1


@needs_pg
async def test_empty_to_rejected_without_new_revision(db_sessionmaker, mailbox):
    first = await generated(db_sessionmaker, mailbox[0])
    with pytest.raises(ApiError) as error:
        await save(
            db_sessionmaker,
            mailbox[0],
            first.task_id,
            edit_request(recipients={"to": [], "cc": ["person@example.test"]}),
        )
    assert error.value.code == "recipients_required"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 1


@needs_pg
async def test_summary_cannot_be_edited_or_reviewed(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    from tests.test_durable_tasks import FakeModel, create_task

    tid, _ = await create_task(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, FakeModel())
    first = await artifact_for(db_sessionmaker, tid)
    with pytest.raises(ApiError) as error:
        await save(db_sessionmaker, mailbox[0], tid)
    assert error.value.code == "draft_required"
    with pytest.raises(ApiError) as error:
        await acknowledge(db_sessionmaker, mailbox[0], first)
    assert error.value.code == "draft_required"
    response = db_client.get(f"/assistant/artifacts/{first.id}", headers=auth_headers(mailbox[0]))
    assert response.status_code == 200
    assert response.json()["review"] is None and response.json()["draft_envelope"] is None
