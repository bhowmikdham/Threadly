"""Typed continuation across HTTP, real PostgreSQL and the durable worker."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.errors import ApiError
from app.assistant import continuation, tasks
from app.assistant.context import capture_thread
from app.assistant.ui_context import capture_view
from app.assistant.worker import run_once
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantTask,
    TaskInput,
    TaskQuestion,
    Thread,
)
from app.schemas.continuation import ClarificationAnswer, TaskInputRequest
from app.schemas.ui_context import UIContextSnapshotRequest
from tests.conftest import needs_pg
from tests.test_contextual_routing import PipelineModel
from tests.test_draft_workflows import DraftModel
from tests.test_durable_tasks import FakeModel, mailbox, request
from tests.test_intent_router import proposal
from tests.test_ui_context import capture_request, visible_mailbox

__all__ = ["mailbox", "visible_mailbox"]


async def start(factory, owner, *, instruction="Write an email", model=None, context_id=None):
    async with factory.begin() as session:
        task = await tasks.submit(
            session,
            owner,
            request(
                context_id,
                instruction=instruction,
                intent_hint=None,
            ),
        )
        task_id = task.id
    await run_once(factory, model or FakeModel())
    async with factory() as session:
        task = await session.get(AssistantTask, task_id)
        question = await continuation.question_view(session, task)
        assert task.state == "needs_clarification" and question
        return task_id, question


def answer(question, *, key="answer-one", **fields):
    return TaskInputRequest(
        schema_version="1.0",
        request_id=key,
        question_id=question["question_id"],
        expected_version=question["expected_version"],
        answer=ClarificationAnswer(**fields),
    )


async def accept(factory, owner, task_id, value):
    async with factory.begin() as session:
        return await continuation.accept_input(session, owner, task_id, value)


@needs_pg
async def test_recipient_answer_keeps_goal_and_original_request_and_builds_effective_draft(
    db_sessionmaker,
    mailbox,
    db_client,
    auth_headers,
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        original = await session.get(AssistantTask, task_id)
        original_hash, release = original.request_hash, original.release
    assert question["fields"] == ["recipients"]
    value = answer(question, recipients=["PERSON@example.test"])
    response = db_client.post(
        f"/assistant/tasks/{task_id}/inputs",
        headers=auth_headers(mailbox[0]),
        json=value.model_dump(),
    )
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "queued"
    assert response.json()["effective_draft_input"]["to"] == ["person@example.test"]
    assert response.json()["instruction"] == "Write an email"
    assert response.json()["input_version"] == 1 and response.json()["question"] is None
    model = DraftModel()
    await run_once(db_sessionmaker, model)
    assert len(model.calls) == 1 and not model.calls[0][1].get("small")
    assert "Write an email" in model.calls[0][0] and "person@example.test" not in model.calls[0][0]
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "succeeded" and task.request_hash == original_hash
        assert task.draft_input is None and task.release == release
        artifact = await session.scalar(select(ArtifactRevision))
        assert artifact.draft_envelope["to"] == ["person@example.test"]
        assert artifact.provenance["input_version"] == 1
        replay = await tasks.submit(
            session,
            mailbox[0],
            request(
                None,
                instruction="Write an email",
                intent_hint=None,
            ),
        )
        assert replay.id == task_id
    # A replay after completion returns current state, never requeues or creates a second draft.
    replay = await accept(db_sessionmaker, mailbox[0], task_id, value)
    assert replay.state == "succeeded"
    assert not await run_once(db_sessionmaker, model)


@needs_pg
async def test_source_answer_resumes_summary_with_owned_snapshot(db_sessionmaker, mailbox):
    task_id, question = await start(
        db_sessionmaker, mailbox[0], instruction="Summarise this thread"
    )
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(question, context_snapshot_id=context.id)
    )
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        artifact = await session.scalar(select(ArtifactRevision))
        assert task.context_snapshot_id is None and task.state == "succeeded"
        assert artifact.payload["context_snapshot_id"] == context.id
    assert len(model.calls) == 1


@needs_pg
async def test_timezone_then_duration_continue_scheduling_without_reclassifying_answer(
    db_sessionmaker,
    mailbox,
):
    route = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["suggest_slots"]
    )
    route["parameters"].update(date_phrase="tomorrow", slot_count=3)
    model = PipelineModel(route)
    task_id, question = await start(
        db_sessionmaker,
        mailbox[0],
        instruction="Find three meeting slots tomorrow",
        model=model,
    )
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(question, timezone="Australia/Melbourne")
    )
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        second = await continuation.question_view(session, task)
        assert second["fields"] == ["duration_minutes"]
        assert second["question_id"] != question["question_id"]
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(second, key="duration", duration_minutes=30)
    )
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.route["decision"]["intent"] == "plan_schedule"
        assert task.route["decision"]["parameters"]["duration_minutes"] == 30
        assert task.state == "unsupported" and task.error_code == "workflow_not_available"
        assert await session.scalar(select(func.count()).select_from(TaskInput)) == 2
    assert (
        len(model.calls) == 1
    )  # Only initial command classification; no Calendar handler installed.


@needs_pg
async def test_concurrent_duplicate_answers_queue_once_and_changed_replay_conflicts(
    db_sessionmaker,
    mailbox,
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    value = answer(question, recipients=["person@example.test"])
    results = await asyncio.gather(
        *[accept(db_sessionmaker, mailbox[0], task_id, value) for _ in range(2)]
    )
    assert {r.input_version for r in results} == {1}
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(TaskInput)) == 1
        assert (await session.get(AssistantJob, task_id)).attempts == 0
        assert (await session.get(TaskQuestion, question["question_id"])).state == "answered"
    with pytest.raises(ApiError) as error:
        await accept(
            db_sessionmaker,
            mailbox[0],
            task_id,
            answer(question, recipients=["other@example.test"]),
        )
    assert error.value.code == "idempotency_conflict"


@needs_pg
@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"expected_version": 1}, "question_changed"),
        ({"question_id": "unrelated"}, "question_not_found"),
    ],
)
async def test_stale_version_or_unrelated_question_has_no_mutation(
    db_sessionmaker,
    mailbox,
    mutation,
    code,
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    value = answer(question, recipients=["person@example.test"]).model_copy(update=mutation)
    with pytest.raises(ApiError) as error:
        await accept(db_sessionmaker, mailbox[0], task_id, value)
    assert error.value.code == code
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).state == "needs_clarification"
        assert await session.scalar(select(func.count()).select_from(TaskInput)) == 0


@needs_pg
async def test_other_owner_cannot_answer_or_read_question(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    response = db_client.post(
        f"/assistant/tasks/{task_id}/inputs",
        headers=auth_headers(mailbox[1]),
        json=answer(question, recipients=["person@example.test"]).model_dump(),
    )
    assert response.status_code == 404
    assert (
        db_client.get(f"/assistant/tasks/{task_id}", headers=auth_headers(mailbox[1])).status_code
        == 404
    )


@needs_pg
async def test_expired_and_cancelled_questions_cannot_resume(db_sessionmaker, mailbox):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    value = answer(question, recipients=["person@example.test"])
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(TaskQuestion).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(ApiError) as error:
        await accept(db_sessionmaker, mailbox[0], task_id, value)
    assert error.value.code == "question_expired"
    async with db_sessionmaker.begin() as session:
        task = await tasks.cancel(session, mailbox[0], task_id, question["expected_version"])
        assert task.state == "cancelled"
    with pytest.raises(ApiError) as error:
        await accept(db_sessionmaker, mailbox[0], task_id, value)
    assert error.value.code == "question_changed"


@needs_pg
async def test_input_transaction_rollback_preserves_question_and_closed_job(
    db_sessionmaker, mailbox
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    value = answer(question, recipients=["person@example.test"])
    with pytest.raises(RuntimeError):
        async with db_sessionmaker.begin() as session:
            await continuation.accept_input(session, mailbox[0], task_id, value)
            raise RuntimeError("Simulated process failure before commit")
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).input_version == 0
        assert (await session.get(AssistantJob, task_id)).state == "done"
        assert (await session.get(TaskQuestion, question["question_id"])).state == "open"
    await accept(db_sessionmaker, mailbox[0], task_id, value)
    await run_once(db_sessionmaker, DraftModel())


@needs_pg
async def test_source_change_requires_explicit_fresh_same_thread_capture(db_sessionmaker, mailbox):
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
    task_id, question = await start(db_sessionmaker, mailbox[0], context_id=context.id)
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).where(Thread.id == mailbox[2]).values(version=1))
    with pytest.raises(ApiError) as error:
        await accept(
            db_sessionmaker,
            mailbox[0],
            task_id,
            answer(question, recipients=["person@example.test"]),
        )
    assert error.value.code == "source_changed"
    async with db_sessionmaker.begin() as session:
        fresh = await capture_thread(session, mailbox[0], "thread-one")
    await accept(
        db_sessionmaker,
        mailbox[0],
        task_id,
        answer(
            question,
            recipients=["person@example.test"],
            context_snapshot_id=fresh.id,
        ),
    )
    async with db_sessionmaker() as session:
        accepted = await session.scalar(select(TaskInput))
        assert (
            accepted.context_snapshot_id == fresh.id and accepted.source_hash == fresh.source_hash
        )
        assert (await session.get(AssistantTask, task_id)).context_snapshot_id == context.id


@needs_pg
async def test_unrequested_answer_field_is_not_a_new_goal(db_sessionmaker, mailbox):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    with pytest.raises(ApiError) as error:
        await accept(db_sessionmaker, mailbox[0], task_id, answer(question, timezone="UTC"))
    assert error.value.code == "answer_field_not_requested"


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"timezone": "Not/AZone"},
        {"duration_minutes": True},
        {"duration_minutes": 0},
        {"recipients": ["name <p@example.test>"]},
        {"recipients": []},
        {"approval": True},
        {"instruction": "yes, send it"},
        {"flow_arn": "arbitrary"},
    ],
)
def test_invalid_or_authorizing_answers_are_rejected(value):
    with pytest.raises(ValidationError):
        ClarificationAnswer.model_validate(value)


@needs_pg
async def test_historical_tasks_do_not_gain_continuation_or_cancel_semantics(
    db_sessionmaker, mailbox
):
    async with db_sessionmaker.begin() as session:
        task = await tasks.submit(session, mailbox[0], request(None))
        task.continuation_release = None
        task_id = task.id
    await run_once(db_sessionmaker, FakeModel())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert await continuation.question_view(session, task) is None
        version = task.version
    with pytest.raises(ApiError) as error:
        async with db_sessionmaker.begin() as session:
            await tasks.cancel(session, mailbox[0], task_id, version)
    assert error.value.code == "task_finished"


@needs_pg
async def test_new_question_rejects_previous_answer_with_new_request_id(db_sessionmaker, mailbox):
    route = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["suggest_slots"]
    )
    task_id, question = await start(
        db_sessionmaker, mailbox[0], instruction="Find meeting slots", model=PipelineModel(route)
    )
    await accept(db_sessionmaker, mailbox[0], task_id, answer(question, timezone="UTC"))
    await run_once(db_sessionmaker, FakeModel())
    with pytest.raises(ApiError) as error:
        await accept(
            db_sessionmaker, mailbox[0], task_id, answer(question, key="late", timezone="UTC")
        )
    assert error.value.code == "question_changed"


@needs_pg
async def test_db_question_owner_fk_rejects_cross_owner_insertion(db_sessionmaker, mailbox):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(
                TaskQuestion(
                    id="forged",
                    task_id=task_id,
                    user_id=mailbox[1],
                    task_version=1,
                    input_version=1,
                    state="open",
                    payload={},
                    expires_at=datetime.now(UTC),
                )
            )
            await session.flush()


@needs_pg
async def test_deleted_effective_source_cascades_task_and_history_without_generation(
    db_sessionmaker, mailbox
):
    task_id, question = await start(
        db_sessionmaker, mailbox[0], instruction="Summarise this thread"
    )
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(question, context_snapshot_id=context.id)
    )
    from app.db.models import ContextSnapshot

    async with db_sessionmaker.begin() as session:
        await session.execute(delete(ContextSnapshot).where(ContextSnapshot.id == context.id))
    model = FakeModel()
    assert not await run_once(db_sessionmaker, model)
    assert not model.calls
    async with db_sessionmaker() as session:
        assert await session.get(AssistantTask, task_id) is None
        assert await session.scalar(select(func.count()).select_from(TaskInput)) == 0
        assert await session.scalar(select(func.count()).select_from(TaskQuestion)) == 0


@needs_pg
async def test_reference_answer_uses_fresh_ui_map_without_reclassifying(
    db_sessionmaker,
    visible_mailbox,
):
    task_id, question = await start(
        db_sessionmaker,
        visible_mailbox[0],
        instruction="What's in the third message?",
    )
    async with db_sessionmaker.begin() as session:
        context = await capture_view(
            session,
            visible_mailbox[0],
            UIContextSnapshotRequest.model_validate(capture_request()),
        )
    await accept(
        db_sessionmaker,
        visible_mailbox[0],
        task_id,
        answer(
            question,
            context_snapshot_id=context.id,
        ),
    )
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    assert not model.calls
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        artifact = await session.scalar(select(ArtifactRevision))
        assert task.state == "succeeded" and task.context_snapshot_id is None
        assert artifact.payload["evidence"][0]["source_id"] == "m2"


@needs_pg
async def test_reply_answer_binds_source_and_recipients_before_generation(db_sessionmaker, mailbox):
    from app.db.models import Message

    task_id, question = await start(db_sessionmaker, mailbox[0], instruction="Draft a reply")
    assert set(question["fields"]) == {"context_snapshot_id", "reply_message_id", "recipients"}
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(Message).values(
                subject="Release",
                reply_metadata={"headers": {"message-id": ["<mail@example.test>"]}},
            )
        )
        context = await capture_thread(session, mailbox[0], "thread-one")
    await accept(
        db_sessionmaker,
        mailbox[0],
        task_id,
        answer(
            question,
            context_snapshot_id=context.id,
            reply_message_id="m1",
            recipients=["p@example.test"],
        ),
    )
    model = DraftModel(reply=True)
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        artifact = await session.scalar(select(ArtifactRevision))
        assert artifact.draft_envelope["reply"]["gmail_message_id"] == "m1"
        assert artifact.payload["content"]["subject"] == "Re: Release"
    assert len(model.calls) == 1


@needs_pg
async def test_another_owners_snapshot_cannot_be_used_as_an_answer(db_sessionmaker, mailbox):
    from app.db.models import ContextSnapshot

    task_id, question = await start(
        db_sessionmaker, mailbox[0], instruction="Summarise this thread"
    )
    async with db_sessionmaker.begin() as session:
        thread = Thread(user_id=mailbox[1], gmail_thread_id="other")
        session.add(thread)
        await session.flush()
        session.add(
            ContextSnapshot(
                id="foreign-context",
                user_id=mailbox[1],
                thread_id=thread.id,
                source_hash="hash",
                payload={},
            )
        )
    with pytest.raises(ApiError) as error:
        await accept(
            db_sessionmaker,
            mailbox[0],
            task_id,
            answer(question, context_snapshot_id="foreign-context"),
        )
    assert error.value.code == "context_not_found"


@needs_pg
async def test_answer_and_cancel_race_does_not_leave_a_cancelled_task_queued(
    db_sessionmaker, mailbox
):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    value = answer(question, recipients=["p@example.test"])

    async def cancel():
        async with db_sessionmaker.begin() as session:
            return await tasks.cancel(session, mailbox[0], task_id, question["expected_version"])

    results = await asyncio.gather(
        accept(db_sessionmaker, mailbox[0], task_id, value), cancel(), return_exceptions=True
    )
    assert sum(isinstance(result, ApiError) for result in results) == 1
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        job = await session.get(AssistantJob, task_id)
        if task.state == "cancelled":
            assert job.state == "done" and task.input_version == 0
        else:
            assert task.state == job.state == "queued" and task.input_version == 1


@needs_pg
async def test_changed_continuation_policy_fails_before_inference(
    db_sessionmaker, mailbox, monkeypatch
):
    async with db_sessionmaker.begin() as session:
        task = await tasks.submit(session, mailbox[0], request(None))
        task_id = task.id
    monkeypatch.setattr(continuation, "VERSION", "unavailable-version")
    model = FakeModel()
    await run_once(db_sessionmaker, model)
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).error_code == "release_unavailable"
    assert not model.calls


@needs_pg
async def test_plain_thread_snapshot_cannot_answer_a_visual_position_question(
    db_sessionmaker, mailbox
):
    task_id, question = await start(
        db_sessionmaker, mailbox[0], instruction="Summarise the first message"
    )
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
    with pytest.raises(ApiError) as error:
        await accept(
            db_sessionmaker, mailbox[0], task_id, answer(question, context_snapshot_id=context.id)
        )
    assert error.value.code == "reference_not_resolved"
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, task_id)).input_version == 0


@needs_pg
async def test_human_input_rounds_are_bounded(db_sessionmaker, mailbox):
    route = proposal(
        intent="plan_schedule",
        output_kind="draft",
        operations=["check_time", "draft_reply"],
    )
    task_id, question = await start(
        db_sessionmaker,
        mailbox[0],
        instruction="Check a meeting time and reply",
        model=PipelineModel(route),
    )
    inputs = [
        {"timezone": "UTC"},
        {"duration_minutes": 30},
        {"date_phrase": "tomorrow"},
        {"recipients": ["p@example.test"]},
        {"time_phrase": "4 PM"},
    ]
    for number, fields in enumerate(inputs):
        await accept(
            db_sessionmaker, mailbox[0], task_id, answer(question, key=f"round-{number}", **fields)
        )
        await run_once(db_sessionmaker, FakeModel())
        async with db_sessionmaker() as session:
            task = await session.get(AssistantTask, task_id)
            question = await continuation.question_view(session, task)
    assert task.input_version == 5 and task.state == "unsupported"
    assert task.error_code == "clarification_limit_reached" and question is None


@needs_pg
async def test_db_input_cannot_attach_another_tasks_question(db_sessionmaker, mailbox):
    task_id, question = await start(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        other = await tasks.submit(
            session, mailbox[1], request(None, instruction="Write an email", intent_hint=None)
        )
        other_id = other.id
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(
                TaskInput(
                    id="forged-input",
                    task_id=other_id,
                    user_id=mailbox[1],
                    question_id=question["question_id"],
                    request_id="forged",
                    request_hash="hash",
                    input_version=1,
                    answer={},
                    effective_fields={},
                )
            )
            await session.flush()


@needs_pg
async def test_worker_claim_retains_typed_timezone_and_original_time_anchor(
    db_sessionmaker, mailbox
):
    route = proposal(
        intent="plan_schedule",
        output_kind="schedule_options",
        operations=["suggest_slots"],
    )
    task_id, question = await start(
        db_sessionmaker,
        mailbox[0],
        instruction="Find three slots tomorrow",
        model=PipelineModel(route),
    )
    async with db_sessionmaker() as session:
        created_at = (await session.get(AssistantTask, task_id)).created_at
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(question, timezone="Australia/Melbourne")
    )
    async with db_sessionmaker.begin() as session:
        claim = await tasks.claim_next(session)
        assert claim.resolved_inputs == {"timezone": "Australia/Melbourne"}
        assert claim.request_created_at == created_at
        assert claim.instruction == "Find three slots tomorrow" and claim.input_version == 1


@needs_pg
async def test_bare_time_answer_creates_am_pm_question_instead_of_ready_schedule(
    db_sessionmaker, mailbox
):
    route = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["check_time"]
    )
    task_id, question = await start(
        db_sessionmaker,
        mailbox[0],
        instruction="Check a meeting time",
        model=PipelineModel(route),
    )
    await accept(
        db_sessionmaker,
        mailbox[0],
        task_id,
        answer(
            question,
            timezone="UTC",
            duration_minutes=30,
            date_phrase="tomorrow",
            time_phrase="4",
        ),
    )
    await run_once(db_sessionmaker, FakeModel())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        next_question = await continuation.question_view(session, task)
        assert next_question["fields"] == ["am_or_pm"]
    await accept(
        db_sessionmaker, mailbox[0], task_id, answer(next_question, key="pm", am_or_pm="PM")
    )
    await run_once(db_sessionmaker, FakeModel())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.route["decision"]["parameters"]["time_phrase"] == "4 PM"
        assert task.state == "unsupported" and task.error_code == "workflow_not_available"


@pytest.mark.parametrize(
    "operations,expected",
    [
        (["suggest_slots", "draft_reply"], "draft"),
        (["plan_actions", "draft_reply"], "draft"),
        (["plan_actions"], "plan"),
        (["suggest_slots"], "schedule_options"),
    ],
)
def test_resolving_clarification_preserves_the_final_requested_output(operations, expected):
    from types import SimpleNamespace

    value = proposal(
        intent="plan_schedule",
        output_kind="clarification",
        operations=operations,
        status="needs_clarification",
        missing_fields=["timezone"],
        clarification="Timezone?",
    )
    context = SimpleNamespace(id="owned", payload={"messages": []})
    resolved = continuation.resolve_route(
        {"decision": value},
        "Find times and prepare my requested output",
        {"timezone": "UTC", "duration_minutes": 30, "date_phrase": "tomorrow"},
        context,
        {"reply": {"bound": True}, "to": ["person@example.test"]},
    )
    assert resolved["decision"]["status"] == "ready"
    assert resolved["decision"]["output_kind"] == expected
    assert resolved["decision"]["operations"] == operations


@pytest.mark.parametrize(
    "instruction,date_phrase,body,expected",
    [
        ("Check tomorrow afternoon", "tomorrow", None, "4 PM"),
        ("Check tomorrow morning", "tomorrow", None, "4 AM"),
        ("Check a meeting time", "tomorrow afternoon", None, "4 PM"),
        ("Check the 4 PM meeting", "tomorrow", None, "4 PM"),
        ("Check a meeting time", "tomorrow", "Can we meet at 4 PM?", "4 PM"),
        ("Check a meeting time", "tomorrow", "At 4 AM or 4 PM?", "4"),
        ("Check a meeting time", "tomorrow", "Not 4 PM.", "4"),
        ("Check afternoon or morning", "tomorrow", None, "4"),
        ("Check a meeting time", "tomorrow", "Office hours are 9 AM to 5 PM.", "4"),
        ("Check a meeting time", "tomorrow", "> Previous meeting at 4 PM", "4"),
        ("Check tomorrow morning", "tomorrow", "Can we meet at 4 PM?", "4 AM"),
    ],
)
def test_time_clarification_uses_explicit_context_before_asking(
    instruction, date_phrase, body, expected
):
    from types import SimpleNamespace

    value = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["check_time"]
    )
    value["parameters"].update(time_phrase="4", date_phrase=date_phrase, duration_minutes=30)
    context = (
        SimpleNamespace(id="owned", payload={"messages": [{"message_id": "m1", "body": body}]})
        if body
        else None
    )
    resolved = continuation.resolve_route(
        {"decision": value}, instruction, {"timezone": "UTC"}, context, None
    )["decision"]
    assert resolved["parameters"]["time_phrase"] == expected
    assert ("am_or_pm" in resolved["missing_fields"]) == (expected == "4")


@needs_pg
async def test_initial_question_omits_am_pm_when_request_already_says_afternoon(
    db_sessionmaker, mailbox
):
    value = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["check_time"]
    )
    value["parameters"].update(time_phrase="4", date_phrase="tomorrow", duration_minutes=30)
    task_id, question = await start(
        db_sessionmaker,
        mailbox[0],
        instruction="Check 4 tomorrow afternoon",
        model=PipelineModel(value),
    )
    assert question["fields"] == ["timezone"]
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.route["decision"]["parameters"]["time_phrase"] == "4 PM"


@pytest.mark.parametrize("target,expected", [(None, "4"), ("m2", "4 PM")])
def test_time_evidence_does_not_pick_an_arbitrary_message_from_a_thread(target, expected):
    value = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["check_time"]
    )
    value["parameters"].update(time_phrase="4", date_phrase="tomorrow", duration_minutes=30)
    snapshot = {
        "messages": [
            {"message_id": "m1", "body": "Meet at 4 AM?"},
            {"message_id": "m2", "body": "Meet at 4 PM?"},
        ]
    }
    result = continuation.resolve_time_context(
        {"decision": value},
        "Check the meeting time",
        {},
        snapshot,
        {"reply_message_id": target} if target else None,
    )
    assert result["decision"]["parameters"]["time_phrase"] == expected
