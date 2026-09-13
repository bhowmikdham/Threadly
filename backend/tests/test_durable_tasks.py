"""Real PostgreSQL lifecycle, ownership, duplicate submission and lease recovery tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.assistant import tasks
from app.assistant.context import CHAR_BUDGET, capture_thread
from app.assistant.summary import make_artifact
from app.assistant.worker import run_once
from app.config import get_settings
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantTask,
    ContextSnapshot,
    Message,
    TaskEvent,
    Thread,
    User,
)
from app.model_client.client import GenResult
from app.model_client.providers import ProviderError
from app.schemas.assistant import AssistantRequest
from tests.conftest import needs_pg

pytestmark = needs_pg

GENERATED = {
    "overview": "The team plans to ship Friday.",
    "decisions": [{"text": "Ship Friday", "sources": [1]}],
    "actions": [{"text": "Prepare the release", "sources": [1]}],
    "open_questions": [],
}


class FakeModel:
    def __init__(self, output=None, error=None):
        self.output = json.dumps(GENERATED) if output is None else output
        self.error = error
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        return self.output, GenResult("fake", "synthetic-summary")


@pytest.fixture
def mailbox(db_sessionmaker):
    async def seed():
        async with db_sessionmaker.begin() as session:
            first, second = (
                User(google_sub="first", email="first@example.test"),
                User(google_sub="second", email="second@example.test"),
            )
            session.add_all([first, second])
            await session.flush()
            thread = Thread(user_id=first.id, gmail_thread_id="thread-one", subject="Release")
            session.add(thread)
            await session.flush()
            # Same timestamp and reverse insertion order exercise stable source ordering.
            for msg_id in ["m2", "m1"]:
                session.add(
                    Message(
                        user_id=first.id,
                        thread_id=thread.id,
                        gmail_msg_id=msg_id,
                        sent_at=datetime(2026, 9, 10, tzinfo=UTC),
                        from_addr="team@example.test",
                        body_clean="Ship Friday.",
                        is_from_user=False,
                    )
                )
            return first.id, second.id, thread.id

    return asyncio.run(seed())


def request(context_id, key="request-one", **changes):
    value = dict(
        schema_version="1.0",
        request_id=key,
        instruction="Summarise this thread",
        intent_hint="summarise",
        context_snapshot_id=context_id,
        continuation=None,
    )
    value.update(changes)
    return AssistantRequest(**value)


async def create_task(factory, user_id, key="request-one"):
    async with factory.begin() as session:
        context = await capture_thread(session, user_id, "thread-one")
        task = await tasks.submit(session, user_id, request(context.id, key))
        return task.id, context.id


async def counts(factory):
    async with factory() as session:
        return [
            await session.scalar(select(func.count()).select_from(t))
            for t in [AssistantTask, AssistantJob, TaskEvent, ArtifactRevision]
        ]


def test_api_snapshot_request_worker_artifact_and_event_replay(
    db_client,
    db_sessionmaker,
    mailbox,
    auth_headers,
):
    user_id, other, _ = mailbox
    headers = auth_headers(user_id)
    captured = db_client.post(
        "/assistant/context-snapshots",
        headers=headers,
        json={"schema_version": "1.0", "thread_id": "thread-one"},
    )
    assert captured.status_code == 201, captured.text
    snapshot = captured.json()
    context_id = snapshot["context_snapshot_id"]
    assert [m["message_id"] for m in snapshot["messages"]] == ["m1", "m2"]
    response = db_client.post(
        "/assistant/requests", headers=headers, json=request(context_id).model_dump()
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    task_id = accepted["task_id"]
    assert accepted["state"] == "queued" and accepted["latest_sequence"] == 1
    assert asyncio.run(counts(db_sessionmaker)) == [1, 1, 1, 0]
    repeated = db_client.post(
        "/assistant/requests", headers=headers, json=request(context_id).model_dump()
    )
    assert repeated.json()["task_id"] == task_id
    assert (
        db_client.post(
            "/assistant/requests",
            headers=headers,
            json=request(context_id, instruction="Summarise this email").model_dump(),
        ).status_code
        == 409
    )

    model = FakeModel()
    assert asyncio.run(run_once(db_sessionmaker, model))
    final = db_client.get(f"/assistant/tasks/{task_id}", headers=headers).json()
    assert final["state"] == "succeeded" and final["version"] == 4
    assert final["latest_sequence"] == 5
    artifact_id = final["artifact_id"]
    artifact = db_client.get(f"/assistant/artifacts/{artifact_id}", headers=headers).json()[
        "artifact"
    ]
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs/implementation-playbook/schemas/artifact.schema.json"
    )
    jsonschema.validate(artifact, json.loads(schema_path.read_text()))
    assert artifact["coverage"] == "partial"
    assert artifact["content"]["actions"][0]["confirmation"] == "inferred"
    assert [e["source_id"] for e in artifact["evidence"]] == ["m1", "m2"]
    assert len(model.calls) == 1 and not asyncio.run(run_once(db_sessionmaker, model))
    events = db_client.get(final["events_url"], headers=headers)
    assert events.status_code == 200
    assert [line for line in events.text.splitlines() if line.startswith("id:")] == [
        "id: 1",
        "id: 2",
        "id: 3",
        "id: 4",
        "id: 5",
    ]
    replay = db_client.get(final["events_url"], headers={**headers, "Last-Event-ID": "2"})
    assert "id: 1" not in replay.text and "id: 3" in replay.text
    assert db_client.get(final["events_url"] + "?after=99", headers=headers).status_code == 409
    assert (
        db_client.get(final["events_url"], headers={**headers, "Last-Event-ID": "bad"}).status_code
        == 422
    )
    for path in [
        f"/assistant/tasks/{task_id}",
        final["events_url"],
        f"/assistant/artifacts/{artifact_id}",
        f"/assistant/context-snapshots/{context_id}",
    ]:
        assert db_client.get(path, headers=auth_headers(other)).status_code == 404
    assert (
        db_client.post(
            "/assistant/requests",
            headers=auth_headers(other),
            json=request(context_id).model_dump(),
        ).status_code
        == 404
    )


async def test_snapshot_is_immutable_after_sync_changes(db_sessionmaker, mailbox):
    task_id, context_id = await create_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Message).values(body_clean="Changed after snapshot."))
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    assert "Ship Friday." in model.calls[0][0]
    assert "Changed after snapshot" not in model.calls[0][0]
    async with db_sessionmaker.begin() as session:
        fresh = await capture_thread(session, mailbox[0], "thread-one")
        old = await session.get(ContextSnapshot, context_id)
        assert fresh.source_hash != old.source_hash
        assert (await session.get(AssistantTask, task_id)).context_snapshot_id == old.id


async def test_large_snapshot_is_bounded_and_discloses_omission(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Message).values(body_clean="x" * (CHAR_BUDGET + 10)))
        context = await capture_thread(session, mailbox[0], "thread-one")
        assert sum(len(m["body"]) for m in context.payload["messages"]) == CHAR_BUDGET
        assert context.payload["omitted_messages"] == 1
        assert context.payload["truncated_messages"] == 1


async def test_concurrent_identical_requests_create_one_job(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        context_id = (await capture_thread(session, mailbox[0], "thread-one")).id

    async def submit():
        async with db_sessionmaker.begin() as session:
            return (await tasks.submit(session, mailbox[0], request(context_id))).id

    ids = await asyncio.gather(*[submit() for _ in range(5)])
    assert len(set(ids)) == 1
    assert await counts(db_sessionmaker) == [1, 1, 1, 0]


async def test_transaction_failure_rolls_back_task_job_and_event(db_sessionmaker, mailbox):
    with pytest.raises(RuntimeError):
        async with db_sessionmaker.begin() as session:
            context = await capture_thread(session, mailbox[0], "thread-one")
            await tasks.submit(session, mailbox[0], request(context.id))
            raise RuntimeError("simulated API failure before commit")
    assert await counts(db_sessionmaker) == [0, 0, 0, 0]


async def test_concurrent_workers_do_not_claim_same_task(db_sessionmaker, mailbox):
    await create_task(db_sessionmaker, mailbox[0])

    async def claim():
        async with db_sessionmaker.begin() as session:
            return await tasks.claim_next(session)

    claims = await asyncio.gather(claim(), claim())
    assert sum(c is not None for c in claims) == 1


async def test_expired_lease_fences_old_worker(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        old = await tasks.claim_next(session)
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(AssistantJob).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    async with db_sessionmaker.begin() as session:
        new = await tasks.claim_next(session)
    assert old.token != new.token and new.task_id == old.task_id
    artifact = make_artifact(json.dumps(GENERATED), old.context_id, old.snapshot)
    async with db_sessionmaker.begin() as session:
        assert not await tasks.finish(session, old, payload=artifact, provenance={})
    async with db_sessionmaker.begin() as session:
        assert await tasks.finish(session, new, payload=artifact, provenance={})
    async with db_sessionmaker.begin() as session:
        assert not await tasks.finish(session, new, payload=artifact, provenance={})
        assert (await session.get(AssistantTask, task_id)).state == "succeeded"
    assert (await counts(db_sessionmaker))[-1] == 1


async def test_cancellation_during_generation_discards_result_without_waiting_for_model(
    db_sessionmaker, mailbox
):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    started, release = asyncio.Event(), asyncio.Event()

    class PausedModel(FakeModel):
        async def generate(self, prompt, **kwargs):
            started.set()
            await release.wait()
            return await super().generate(prompt, **kwargs)

    worker = asyncio.create_task(run_once(db_sessionmaker, PausedModel()))
    await asyncio.wait_for(started.wait(), 3)
    try:
        async with db_sessionmaker.begin() as session:
            # A held DB transaction/row lock during inference would block this.
            task = await asyncio.wait_for(tasks.cancel(session, mailbox[0], task_id, 3), 2)
            assert task.state == "cancelled"
    finally:
        release.set()
        await worker
    assert (await counts(db_sessionmaker))[-1] == 0


def test_cancel_api_checks_version_and_is_retryable(
    db_client, db_sessionmaker, mailbox, auth_headers
):
    task_id, _ = asyncio.run(create_task(db_sessionmaker, mailbox[0]))
    path = f"/assistant/tasks/{task_id}/cancel"
    headers = auth_headers(mailbox[0])
    assert db_client.post(path, headers=headers, json={"expected_version": 9}).status_code == 409
    for _ in range(2):
        r = db_client.post(path, headers=headers, json={"expected_version": 1})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "cancelled"
    assert not asyncio.run(run_once(db_sessionmaker, FakeModel()))


async def test_provider_retry_budget_is_persisted(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    for attempt in range(1, 4):
        assert await run_once(
            db_sessionmaker, FakeModel(error=ProviderError("private source data"))
        )
        async with db_sessionmaker.begin() as session:
            job = await session.get(AssistantJob, task_id)
            task = await session.get(AssistantTask, task_id)
            assert job.attempts == attempt
            assert task.state == ("failed" if attempt == 3 else "queued")
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not await run_once(db_sessionmaker, FakeModel())
    assert await counts(db_sessionmaker) == [1, 1, 8, 0]


async def test_repeated_crashes_exhaust_attempts(db_sessionmaker, mailbox):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    for _ in range(3):
        async with db_sessionmaker.begin() as session:
            assert await tasks.claim_next(session)
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(AssistantJob).values(
                    lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
    async with db_sessionmaker.begin() as session:
        assert await tasks.claim_next(session) is None
        task = await session.get(AssistantTask, task_id)
        assert task.state == "failed" and task.error_code == "attempts_exhausted"


async def test_release_change_fails_without_calling_a_different_model(
    db_sessionmaker, mailbox, monkeypatch
):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    monkeypatch.setattr(get_settings(), "model_main", "changed-model")
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    assert not model.calls
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).error_code == "release_unavailable"


@pytest.mark.parametrize(
    "output", ['{"overview":"bad"}', "[]", '"summary"', "private malformed text"]
)
async def test_invalid_generation_never_publishes_artifact(db_sessionmaker, mailbox, output):
    task_id, _ = await create_task(db_sessionmaker, mailbox[0])
    await run_once(db_sessionmaker, FakeModel(output=output))
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "failed" and task.error_code == "invalid_summary_output"
    assert (await counts(db_sessionmaker))[-1] == 0


async def test_cross_owner_foreign_key_rejects_direct_write(db_sessionmaker, mailbox):
    _, context_id = await create_task(db_sessionmaker, mailbox[0])
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(
                AssistantTask(
                    id="invalid-owner",
                    user_id=mailbox[1],
                    request_id="key",
                    request_hash="a" * 64,
                    instruction="Summarise this",
                    context_snapshot_id=context_id,
                    state="queued",
                    version=1,
                    latest_sequence=1,
                    release={},
                )
            )


async def test_account_deletion_cascades_and_fences_worker(db_sessionmaker, mailbox):
    await create_task(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        claim = await tasks.claim_next(session)
    async with db_sessionmaker.begin() as session:
        await session.execute(delete(User).where(User.id == mailbox[0]))
    async with db_sessionmaker.begin() as session:
        assert not await tasks.finish(session, claim, payload={}, provenance={})
    assert await counts(db_sessionmaker) == [0, 0, 0, 0]


async def test_compound_request_is_durably_accepted_for_routing(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
        task = await tasks.submit(
            session, mailbox[0], request(context.id, instruction="Summarise and send a reply")
        )
        assert task.state == "queued" and task.route is None
    assert await counts(db_sessionmaker) == [1, 1, 1, 0]


def test_history_pagination_survives_new_tasks_and_is_owner_scoped(
    db_client,
    db_sessionmaker,
    mailbox,
    auth_headers,
):
    ids = [asyncio.run(create_task(db_sessionmaker, mailbox[0], f"key-{i}"))[0] for i in range(3)]
    headers = auth_headers(mailbox[0])
    page = db_client.get("/assistant/tasks?page_size=2", headers=headers).json()
    assert [t["task_id"] for t in page["tasks"]] == list(reversed(ids[1:]))
    cursor = page["next_cursor"]
    asyncio.run(create_task(db_sessionmaker, mailbox[0], "new-arrival"))
    second = db_client.get(f"/assistant/tasks?page_size=2&cursor={cursor}", headers=headers).json()
    assert [t["task_id"] for t in second["tasks"]] == [ids[0]]
    assert second["next_cursor"] is None
    assert db_client.get("/assistant/tasks?state=failed", headers=headers).json()["tasks"] == []
    assert db_client.get("/assistant/tasks", headers=auth_headers(mailbox[1])).json()["tasks"] == []
    assert (
        db_client.get(
            f"/assistant/tasks?cursor={cursor}", headers=auth_headers(mailbox[1])
        ).status_code
        == 404
    )


async def test_worker_timeout_releases_control_and_schedules_bounded_retry(
    db_sessionmaker,
    mailbox,
    monkeypatch,
):
    import app.assistant.worker as worker

    task_id, _ = await create_task(db_sessionmaker, mailbox[0])

    class StalledModel:
        async def generate(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(worker, "GENERATION_TIMEOUT_SECONDS", 0.01)
    assert await asyncio.wait_for(worker.run_once(db_sessionmaker, StalledModel()), 3)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        job = await session.get(AssistantJob, task_id)
        assert task.state == "queued" and task.error_code == "upstream_model_unavailable"
        assert job.attempts == 1 and job.lease_token is None
