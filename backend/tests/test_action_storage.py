"""Durable action primitives using real PostgreSQL. No provider or live approvals."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.actions import service, state
from app.api.errors import ApiError
from app.assistant import draft_review
from app.assistant.worker import run_once
from app.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionJob,
    ArtifactRevision,
    AssistantAction,
    AssistantTask,
    TaskEvent,
    Thread,
    User,
)
from tests.conftest import needs_pg
from tests.test_draft_review import edit_request, generated
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]


def candidate(**changes):
    return {
        "request_id": "proposal-one",
        "expected_revision": 1,
        "action_type": "send_email",
        "payload_schema": "test-candidate-1.0",
        "payload": {"body": "Synthetic private body", "to": ["recipient@example.test"]},
        "source_versions": {"fixture_only": True},
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        **changes,
    }


async def propose(factory, owner, artifact, values):
    async with factory.begin() as session:
        return await service.propose(session, owner, artifact.id, **values)


async def approval_fixture(factory, action, *, state_name="approved"):
    """Storage fixture, not the unimplemented HTTP approval/executor lifecycle."""
    async with factory.begin() as session:
        approval = ActionApproval(
            id=str(uuid4()),
            action_id=action.id,
            user_id=action.user_id,
            action_version=1,
            payload_hash=action.payload_hash,
            request_id=str(uuid4()),
            request_hash="a" * 64,
            expires_at=action.expires_at,
        )
        session.add(approval)
        await session.flush()
        session.add(ActionJob(action_id=action.id, user_id=action.user_id, approval_id=approval.id))
        await session.execute(
            update(AssistantAction)
            .where(AssistantAction.id == action.id)
            .values(state=state_name, version=2)
        )
        return approval


def attempt(action, approval, number=1, **changes):
    return ActionAttempt(
        **{
            "id": str(uuid4()),
            "action_id": action.id,
            "user_id": action.user_id,
            "approval_id": approval.id,
            "number": number,
            "action_version": 2,
            "lease_token": str(uuid4()),
            "state": "dispatched",
            "dispatch_intent_at": datetime.now(UTC),
            "provider_identifiers": {"message_id": "synthetic-before-dispatch@example.test"},
            **changes,
        }
    )


@needs_pg
async def test_proposal_deduplication_rollback_and_no_generation_job(db_sessionmaker, mailbox):
    artifact = await generated(db_sessionmaker, mailbox[0])
    values = candidate()

    async def accept():
        return await propose(db_sessionmaker, mailbox[0], artifact, values)

    first, second = await asyncio.gather(accept(), accept())
    assert first.id == second.id and first.state == "proposed"
    assert first.payload_hash == service.candidate_hash(values["payload_schema"], values["payload"])
    with pytest.raises(ApiError) as conflict:
        await propose(
            db_sessionmaker, mailbox[0], artifact, {**values, "payload": {"body": "changed"}}
        )
    assert conflict.value.code == "idempotency_conflict"
    async with db_sessionmaker() as session:
        await service.propose(session, mailbox[0], artifact.id, **candidate(request_id="rollback"))
        await session.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0
        events = (
            await session.scalars(select(TaskEvent).where(TaskEvent.kind == "action.proposed"))
        ).all()
        assert len(events) == 1 and "Synthetic private body" not in str(events[0].payload)
    assert not await run_once(db_sessionmaker)  # action records never enter generation queue


@needs_pg
async def test_other_owner_and_noncurrent_artifact_rejected(db_sessionmaker, mailbox):
    artifact = await generated(db_sessionmaker, mailbox[0])
    with pytest.raises(ApiError) as failure:
        await propose(db_sessionmaker, mailbox[1], artifact, candidate())
    assert failure.value.status == 404
    action = await propose(db_sessionmaker, mailbox[0], artifact, candidate())
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as failure:
            await service.owned_action(session, mailbox[1], action.id, lock=True)
        assert failure.value.status == 404
    async with db_sessionmaker.begin() as session:
        await draft_review.edit(session, mailbox[0], artifact.task_id, edit_request())
    with pytest.raises(ApiError) as failure:
        await propose(db_sessionmaker, mailbox[0], artifact, candidate(request_id="stale"))
    assert failure.value.code == "revision_conflict"


@pytest.mark.parametrize("action_state", ["proposed", "approved", "executing", "outcome_unknown"])
@needs_pg
async def test_edit_supersedes_only_before_dispatch_and_keeps_exact_payload(
    db_sessionmaker, mailbox, action_state
):
    artifact = await generated(db_sessionmaker, mailbox[0])
    values = candidate()
    action = await propose(db_sessionmaker, mailbox[0], artifact, values)
    original_hash, original_payload = action.payload_hash, copy.deepcopy(action.payload)
    if action_state != "proposed":
        await approval_fixture(db_sessionmaker, action, state_name=action_state)
    async with db_sessionmaker.begin() as session:
        await draft_review.edit(session, mailbox[0], artifact.task_id, edit_request())
    async with db_sessionmaker() as session:
        saved = await session.get(AssistantAction, action.id)
        assert saved.state == ("superseded" if action_state in state.PRE_DISPATCH else action_state)
        assert saved.payload == original_payload and saved.payload_hash == original_hash
        assert saved.artifact_id == artifact.id
        job = await session.get(ActionJob, action.id)
        if job:
            assert job.state == ("done" if action_state == "approved" else "held")
    # Replaying the same proposal after editing returns saved superseded/in-flight state.
    assert (await propose(db_sessionmaker, mailbox[0], artifact, values)).id == action.id


@needs_pg
async def test_stale_competing_transitions_and_expiry(db_sessionmaker, mailbox):
    artifact = await generated(db_sessionmaker, mailbox[0])
    action = await propose(db_sessionmaker, mailbox[0], artifact, candidate())

    async def stop(target):
        async with db_sessionmaker.begin() as session:
            return await service.stop_before_dispatch(
                session, mailbox[0], action.id, expected_version=1, state=target
            )

    results = await asyncio.gather(stop("cancelled"), stop("rejected"), return_exceptions=True)
    assert sum(isinstance(r, AssistantAction) for r in results) == 1
    errors = [r for r in results if isinstance(r, ApiError)]
    assert len(errors) == 1 and errors[0].code == "version_conflict"
    winner = next(r for r in results if isinstance(r, AssistantAction))
    assert (await stop(winner.state)).id == action.id
    with pytest.raises(ApiError) as failure:
        await stop("approved")
    assert failure.value.code == "action_execution_unavailable"
    another = await propose(db_sessionmaker, mailbox[0], artifact, candidate(request_id="expire"))
    async with db_sessionmaker.begin() as session:
        with pytest.raises(ApiError) as failure:
            await service.stop_before_dispatch(
                session, mailbox[0], another.id, expected_version=1, state="expired"
            )
        assert failure.value.code == "action_not_expired"


@needs_pg
async def test_draft_edit_and_proposal_race_never_leaves_stale_live_proposal(
    db_sessionmaker, mailbox
):
    artifact = await generated(db_sessionmaker, mailbox[0])

    async def edit():
        async with db_sessionmaker.begin() as session:
            return await draft_review.edit(session, mailbox[0], artifact.task_id, edit_request())

    results = await asyncio.gather(
        propose(db_sessionmaker, mailbox[0], artifact, candidate()), edit(), return_exceptions=True
    )
    if isinstance(results[0], ApiError):
        assert results[0].code == "revision_conflict"
    else:
        async with db_sessionmaker() as session:
            assert (await session.get(AssistantAction, results[0].id)).state == "superseded"
    assert not isinstance(results[1], Exception)


@needs_pg
async def test_action_approval_job_and_attempt_owner_constraints(db_sessionmaker, mailbox):
    artifact = await generated(db_sessionmaker, mailbox[0])
    action = await propose(db_sessionmaker, mailbox[0], artifact, candidate())
    approval = await approval_fixture(db_sessionmaker, action)
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(AssistantAction)
                .where(AssistantAction.id == action.id)
                .values(user_id=mailbox[1])
            )
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(
                ActionApproval(
                    id=str(uuid4()),
                    action_id=action.id,
                    user_id=mailbox[1],
                    action_version=1,
                    payload_hash=action.payload_hash,
                    request_id="foreign",
                    request_hash="b" * 64,
                    expires_at=action.expires_at,
                )
            )
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(attempt(action, approval, user_id=mailbox[1]))
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            await session.execute(
                update(ActionJob).where(ActionJob.action_id == action.id).values(user_id=mailbox[1])
            )
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(
                ActionApproval(
                    id=str(uuid4()),
                    action_id=action.id,
                    user_id=action.user_id,
                    action_version=2,
                    payload_hash="0" * 64,
                    request_id="wrong-hash",
                    request_hash="b" * 64,
                    expires_at=action.expires_at,
                )
            )


@needs_pg
async def test_only_one_unresolved_attempt_survives_unknown_outcome(db_sessionmaker, mailbox):
    artifact = await generated(db_sessionmaker, mailbox[0])
    action = await propose(db_sessionmaker, mailbox[0], artifact, candidate())
    approval = await approval_fixture(db_sessionmaker, action)
    initial = attempt(action, approval)
    async with db_sessionmaker.begin() as session:
        session.add(initial)
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(attempt(action, approval, 2))
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(ActionAttempt)
            .where(ActionAttempt.id == initial.id)
            .values(state="outcome_unknown")
        )
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            session.add(attempt(action, approval, 2))
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(ActionAttempt)
            .where(ActionAttempt.id == initial.id)
            .values(state="failed", evidence={"fixture": "definite rejection"})
        )
        session.add(attempt(action, approval, 2))


@pytest.mark.parametrize("table", [User, Thread, AssistantTask, ArtifactRevision])
@needs_pg
async def test_source_and_account_cascades_cannot_erase_action_history(
    db_sessionmaker, mailbox, table
):
    artifact = await generated(db_sessionmaker, mailbox[0], reply=True)
    action = await propose(db_sessionmaker, mailbox[0], artifact, candidate())
    await approval_fixture(db_sessionmaker, action, state_name="outcome_unknown")
    async with db_sessionmaker.begin() as session:
        blockers = await service.source_deletion_blockers(session, mailbox[0], artifact.task_id)
        assert blockers == [
            {
                "action_id": action.id,
                "state": "outcome_unknown",
                "reason": "unresolved_external_outcome",
            }
        ]
    value = {
        User: mailbox[0],
        Thread: mailbox[2],
        AssistantTask: artifact.task_id,
        ArtifactRevision: artifact.id,
    }[table]
    with pytest.raises(IntegrityError):
        async with db_sessionmaker.begin() as session:
            await session.execute(delete(table).where(table.id == value))
    async with db_sessionmaker() as session:
        assert await session.get(AssistantAction, action.id)
        assert await session.get(ArtifactRevision, artifact.id)


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {}},
        {"payload": {"x": float("nan")}},
        {"payload": {"x": "x" * 128001}},
        {"expected_revision": True},
        {"expires_at": datetime.now()},
        {"expires_at": datetime.now(UTC) - timedelta(hours=1)},
        {"expires_at": datetime.now(UTC) + timedelta(days=2)},
        {"action_type": "arbitrary"},
        {"payload_schema": ""},
    ],
)
@needs_pg
async def test_invalid_candidates_leave_no_action_rows(db_sessionmaker, mailbox, change):
    artifact = await generated(db_sessionmaker, mailbox[0])
    with pytest.raises(ApiError):
        await propose(db_sessionmaker, mailbox[0], artifact, candidate(**change))
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0


def test_state_graph_never_blindly_retries_unknown_or_terminal_outcomes():
    assert state.allows("proposed", "approved")
    assert state.allows("executing", "outcome_unknown")
    assert state.allows("outcome_unknown", "succeeded")
    for before in ["outcome_unknown", *state.TERMINAL]:
        assert not state.allows(before, "approved") and not state.allows(before, "executing")
    assert not state.allows("proposed", "succeeded")
