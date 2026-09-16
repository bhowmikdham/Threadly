"""Owned decision APIs, exact approval transactions and simulated dispatch races."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.actions import approval, email_preview, service
from app.api.errors import ApiError
from app.assistant import draft_review
from app.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionDecision,
    ActionJob,
    AssistantAction,
    TaskEvent,
    User,
)
from app.schemas.actions import ActionDecisionRequest, ApproveActionRequest
from tests.conftest import needs_pg
from tests.test_draft_review import edit_request, generated, save
from tests.test_durable_tasks import mailbox
from tests.test_email_previews import accept, connect

__all__ = ["mailbox"]


def approve_request(action, **changes):
    return ApproveActionRequest(
        **{
            "request_id": "approve-one",
            "expected_version": 1,
            "payload_hash": action.payload_hash,
            **changes,
        }
    )


def stop_request(version=1, key="stop-one"):
    return ActionDecisionRequest(request_id=key, expected_version=version)


async def candidate(factory, owner):
    await connect(factory, owner)
    async with factory.begin() as session:
        (await session.get(User, owner)).google_scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ]
    artifact = await generated(factory, owner)
    return await accept(factory, owner, artifact)


async def approve(factory, owner, action, value=None):
    async with factory.begin() as session:
        return await approval.approve(
            session, owner, action.id, value or approve_request(action), execution_enabled=True
        )


async def stop(factory, owner, action, operation="cancel", value=None):
    async with factory.begin() as session:
        return await approval.stop(session, owner, action.id, operation, value or stop_request())


async def fake_dispatch(factory, owner, action_id):
    """Only in tests: atomically persist the cutoff; never performs a provider call."""
    async with factory.begin() as session:
        _, action = await service.owned_action(session, owner, action_id, lock=True)
        job = await session.get(ActionJob, action.id, with_for_update=True, populate_existing=True)
        if action.state != "approved" or job.state != "queued":
            return False
        now = await session.scalar(select(func.clock_timestamp()))
        token = str(uuid4())
        session.add(
            ActionAttempt(
                id=str(uuid4()),
                action_id=action.id,
                user_id=owner,
                approval_id=job.approval_id,
                number=1,
                action_version=action.version,
                lease_token=token,
                state="dispatched",
                dispatch_intent_at=now,
                provider_identifiers={"message_id": action.payload["preview"]["message_id"]},
            )
        )
        await session.flush()
        action.state, action.version = "executing", action.version + 1
        job.state, job.lease_token, job.lease_expires_at = (
            "running",
            token,
            now + timedelta(minutes=1),
        )
        return True


@needs_pg
async def test_duplicate_approvals_one_job_and_replay_after_cancellation(db_sessionmaker, mailbox):
    action = await candidate(db_sessionmaker, mailbox[0])
    results = await asyncio.gather(
        *[approve(db_sessionmaker, mailbox[0], action) for _ in range(2)]
    )
    assert results[0] == results[1]
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 1
        stored = await session.get(AssistantAction, action.id)
        assert (
            stored.state == "approved" and stored.version == 2 and stored.payload == action.payload
        )
        assert (await session.get(ActionJob, action.id)).state == "queued"
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0
    await stop(db_sessionmaker, mailbox[0], action, value=stop_request(2))
    assert not await fake_dispatch(db_sessionmaker, mailbox[0], action.id)
    assert await approve(db_sessionmaker, mailbox[0], action) == results[0]
    with pytest.raises(ApiError) as conflict:
        await approve(
            db_sessionmaker, mailbox[0], action, approve_request(action, payload_hash="a" * 64)
        )
    assert conflict.value.code == "idempotency_conflict"


@needs_pg
async def test_public_approval_closed_and_stop_api_owned_strict(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    action = await candidate(db_sessionmaker, mailbox[0])
    url = f"/assistant/actions/{action.id}"
    response = db_client.post(
        url + "/approve",
        json=approve_request(action).model_dump(),
        headers=auth_headers(mailbox[0]),
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "action_execution_unavailable"
    )
    for operation in ("approve", "reject", "cancel"):
        body = (
            approve_request(action).model_dump()
            if operation == "approve"
            else stop_request().model_dump()
        )
        assert db_client.post(url + "/" + operation, json=body).status_code == 401
        assert (
            db_client.post(
                url + "/" + operation, json=body, headers=auth_headers(mailbox[1])
            ).status_code
            == 404
        )
        assert (
            db_client.post(
                "/assistant/actions/missing/" + operation,
                json=body,
                headers=auth_headers(mailbox[0]),
            ).status_code
            == 404
        )
        for field in ("body", "recipients", "execution_enabled", "user_id"):
            response = db_client.post(
                url + "/" + operation,
                json={**body, field: "PRIVATE"},
                headers=auth_headers(mailbox[0]),
            )
            assert response.status_code == 422 and "PRIVATE" not in response.text
    response = db_client.post(
        url + "/reject", json=stop_request().model_dump(), headers=auth_headers(mailbox[0])
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["decision"] == "rejected" and result["action"]["state"] == "rejected"
    assert result["action"]["allowed_operations"] == []
    assert (
        db_client.post(
            url + "/reject", json=stop_request().model_dump(), headers=auth_headers(mailbox[0])
        ).json()
        == result
    )
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionDecision)) == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ("hash", "payload_conflict"),
        ("version", "version_conflict"),
        ("scope", "action_approval_blocked"),
        ("account", "action_approval_blocked"),
        ("expired", "action_approval_blocked"),
        ("edit", "version_conflict"),
        ("rejected", "version_conflict"),
        ("disconnected", "action_approval_blocked"),
    ],
)
@needs_pg
async def test_approval_preconditions_no_orphan(db_sessionmaker, mailbox, change, code):
    action = await candidate(db_sessionmaker, mailbox[0])
    value = approve_request(action)
    if change == "hash":
        value = approve_request(action, payload_hash="a" * 64)
    if change == "version":
        value = approve_request(action, expected_version=8)
    if change == "edit":
        await save(db_sessionmaker, mailbox[0], action.task_id)
    if change == "rejected":
        await stop(db_sessionmaker, mailbox[0], action, "reject")
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, mailbox[0])
        if change == "scope":
            user.google_scopes = []
        if change == "account":
            user.google_account_version += 1
        if change == "disconnected":
            user.google_connected = False
        if change == "expired":
            (await session.get(AssistantAction, action.id)).expires_at = await session.scalar(
                select(func.clock_timestamp() - timedelta(seconds=1))
            )
    with pytest.raises(ApiError) as failed:
        await approve(db_sessionmaker, mailbox[0], action, value)
    assert failed.value.code == code
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0


@needs_pg
async def test_queue_failure_rolls_back_approval_state_and_events(db_sessionmaker, mailbox):
    action = await candidate(db_sessionmaker, mailbox[0])

    def fail_queue(mapper, connection, target):
        target.approval_id = "missing-approval"  # Real FK failure during job insert.

    event.listen(ActionJob, "before_insert", fail_queue)
    try:
        with pytest.raises(IntegrityError):
            await approve(db_sessionmaker, mailbox[0], action)
    finally:
        event.remove(ActionJob, "before_insert", fail_queue)
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action.id)).state == "proposed"
        for model in (ActionApproval, ActionJob):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert not any(
            e.payload.get("state") == "approved"
            for e in (await session.scalars(select(TaskEvent))).all()
        )
    # Caller rollback also removes a fully flushed approval/job.
    async with db_sessionmaker() as session:
        await approval.approve(
            session, mailbox[0], action.id, approve_request(action), execution_enabled=True
        )
        await session.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0


@needs_pg
async def test_approval_edit_race_never_leaves_runnable_stale_action(db_sessionmaker, mailbox):
    action = await candidate(db_sessionmaker, mailbox[0])

    async def edit():
        async with db_sessionmaker.begin() as session:
            return await draft_review.edit(session, mailbox[0], action.task_id, edit_request())

    results = await asyncio.gather(
        approve(db_sessionmaker, mailbox[0], action), edit(), return_exceptions=True
    )
    assert not isinstance(results[1], Exception)
    if isinstance(results[0], Exception):
        assert isinstance(results[0], ApiError) and results[0].code == "version_conflict"
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action.id)).state == "superseded"
        job = await session.get(ActionJob, action.id)
        assert job is None or job.state == "done"


@needs_pg
async def test_cancel_dispatch_race_and_late_cancel_preserves_recovery(db_sessionmaker, mailbox):
    action = await candidate(db_sessionmaker, mailbox[0])
    await approve(db_sessionmaker, mailbox[0], action)
    dispatched, cancelled = await asyncio.gather(
        fake_dispatch(db_sessionmaker, mailbox[0], action.id),
        stop(db_sessionmaker, mailbox[0], action, value=stop_request(2)),
        return_exceptions=True,
    )
    assert not isinstance(dispatched, Exception)
    if not dispatched:
        assert cancelled["decision"] == "cancelled"
        return
    assert isinstance(cancelled, ApiError) and cancelled.code == "version_conflict"
    result = await stop(db_sessionmaker, mailbox[0], action, value=stop_request(3))
    assert result["decision"] == "cancellation_requested"
    async with db_sessionmaker.begin() as session:
        _, current = await service.owned_action(session, mailbox[0], action.id, lock=True)
        attempt = await session.scalar(
            select(ActionAttempt).where(ActionAttempt.action_id == action.id)
        )
        attempt.state = "outcome_unknown"
        current.state, current.version = "outcome_unknown", 4
    assert await stop(db_sessionmaker, mailbox[0], action, value=stop_request(3)) == result
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
        assert view["state"] == "outcome_unknown" and view["cancellation_requested"]
        assert (await session.get(ActionJob, action.id)).state == "running"
        assert await session.scalar(select(func.count()).select_from(ActionDecision)) == 1


@needs_pg
async def test_late_cancel_and_idempotency_conflicts(db_sessionmaker, mailbox):
    action = await candidate(db_sessionmaker, mailbox[0])
    await approve(db_sessionmaker, mailbox[0], action)
    assert await fake_dispatch(db_sessionmaker, mailbox[0], action.id)
    results = await asyncio.gather(
        *[stop(db_sessionmaker, mailbox[0], action, value=stop_request(3)) for _ in range(2)]
    )
    assert results[0] == results[1] and results[0]["decision"] == "cancellation_requested"
    with pytest.raises(ApiError) as failed:
        await stop(db_sessionmaker, mailbox[0], action, value=stop_request(4))
    assert failed.value.code == "idempotency_conflict"
    with pytest.raises(ApiError) as failed:
        await stop(db_sessionmaker, mailbox[0], action, "reject", stop_request(3))
    assert failed.value.code == "action_state_conflict"
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
        assert view["state"] == "executing" and view["version"] == 3
        assert view["cancellation_requested"] and view["allowed_operations"] == []
        assert view["authorization"] == "exact_payload_approval" and not view["sending_available"]
        assert (
            len(
                (
                    await session.scalars(
                        select(TaskEvent).where(TaskEvent.kind == "action.cancellation_requested")
                    )
                ).all()
            )
            == 1
        )


@needs_pg
async def test_user_lock_fences_disconnect(db_sessionmaker, mailbox, monkeypatch):
    action = await candidate(db_sessionmaker, mailbox[0])
    acquired = asyncio.Event()
    original = service.owned_action

    async def observe(*args, **kwargs):
        result = await original(*args, **kwargs)
        acquired.set()
        return result

    monkeypatch.setattr(service, "owned_action", observe)
    async with db_sessionmaker.begin() as locker:
        user = await locker.get(User, mailbox[0], with_for_update=True)
        future = asyncio.create_task(approve(db_sessionmaker, mailbox[0], action))
        await asyncio.wait_for(acquired.wait(), timeout=5)
        assert not future.done()
        user.google_connected = False
    with pytest.raises(ApiError) as failed:
        await future
    assert failed.value.code == "action_approval_blocked"


@needs_pg
async def test_approval_rechecks_effective_context_and_payload(db_sessionmaker, mailbox):
    from sqlalchemy import update

    from app.assistant.context import capture_thread
    from app.db.models import ArtifactRevision, Thread
    from tests.test_email_previews import request as preview_request

    action = await candidate(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        source = await capture_thread(session, mailbox[0], "thread-one")
        draft = await session.get(ArtifactRevision, action.artifact_id)
        draft.payload = {**draft.payload, "context_snapshot_id": source.id}
    # Existing frozen proposal fails artifact integrity rather than silently adopting context.
    with pytest.raises(ApiError) as failure:
        await approve(db_sessionmaker, mailbox[0], action)
    assert "artifact_changed" in failure.value.detail["blockers"]
    fresh = await accept(db_sessionmaker, mailbox[0], draft, preview_request("with-source"))
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=Thread.version + 1))
    with pytest.raises(ApiError) as failure:
        await approve(db_sessionmaker, mailbox[0], fresh)
    assert "source_changed" in failure.value.detail["blockers"]


@needs_pg
async def test_request_keys_bind_action_and_operation(db_sessionmaker, mailbox):
    from app.db.models import ArtifactRevision
    from tests.test_email_previews import request as preview_request

    first = await candidate(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        draft = await session.get(ArtifactRevision, first.artifact_id)
    second = await accept(db_sessionmaker, mailbox[0], draft, preview_request("second"))
    await approve(db_sessionmaker, mailbox[0], first)
    with pytest.raises(ApiError) as failed:
        await approve(db_sessionmaker, mailbox[0], second)
    assert failed.value.code == "idempotency_conflict"
    await stop(db_sessionmaker, mailbox[0], first, value=stop_request(2))
    with pytest.raises(ApiError) as failed:
        await stop(db_sessionmaker, mailbox[0], second)
    assert failed.value.code == "idempotency_conflict"
    # A rejection key lives in a separate operation namespace.
    assert (await stop(db_sessionmaker, mailbox[0], second, "reject"))["decision"] == "rejected"


@needs_pg
async def test_approval_replay_api_does_not_enqueue_again(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    action = await candidate(db_sessionmaker, mailbox[0])
    await approve(db_sessionmaker, mailbox[0], action)
    await stop(db_sessionmaker, mailbox[0], action, value=stop_request(2))
    response = db_client.post(
        f"/assistant/actions/{action.id}/approve",
        json=approve_request(action).model_dump(),
        headers=auth_headers(mailbox[0]),
    )
    assert response.status_code == 202
    assert response.json()["decision"] == "approved"
    assert response.json()["action"]["state"] == "cancelled"
    async with db_sessionmaker() as session:
        assert (await session.get(ActionJob, action.id)).state == "done"
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 1


@needs_pg
async def test_public_cancel_and_stop_rollback(db_sessionmaker, mailbox, db_client, auth_headers):
    action = await candidate(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        await approval.stop(session, mailbox[0], action.id, "cancel", stop_request())
        await session.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionDecision)) == 0
        assert (await session.get(AssistantAction, action.id)).state == "proposed"
    response = db_client.post(
        f"/assistant/actions/{action.id}/cancel",
        json=stop_request().model_dump(),
        headers=auth_headers(mailbox[0]),
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "cancelled"
    assert response.json()["action"]["state"] == "cancelled"
    assert response.json()["action"]["version"] == 2
