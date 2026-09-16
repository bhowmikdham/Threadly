"""Exactly one mock HTTP POST, transaction boundaries, fences and crash recovery."""

import asyncio
import base64
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.actions import email_preview, gmail_sender, worker
from app.api.errors import ApiError
from app.config import Settings, get_settings
from app.db.models import ActionAttempt, ActionJob, AssistantAction, TaskEvent, User
from tests.conftest import needs_pg
from tests.test_action_approval import approve, candidate, stop, stop_request
from tests.test_draft_review import save
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]


@pytest.fixture
def writes(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_writes_enabled", True)


async def token(owner):
    return "SYNTHETIC_SECRET_TOKEN"


async def ready(factory, owner):
    action = await candidate(factory, owner)
    await approve(factory, owner, action)
    return action


async def expire(factory, action_id):
    async with factory.begin() as session:
        job = await session.get(ActionJob, action_id)
        job.lease_expires_at = await session.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=1
        )


async def read(factory, action_id):
    async with factory() as session:
        action = await session.get(AssistantAction, action_id)
        job = await session.get(ActionJob, action_id)
        attempts = (
            await session.scalars(select(ActionAttempt).where(ActionAttempt.action_id == action_id))
        ).all()
        return action, job, attempts


@pytest.mark.parametrize(
    "status,body,state",
    [
        (200, {"id": "msg-one", "threadId": "thread-one"}, "succeeded"),
        (200, {"id": "msg-one"}, "outcome_unknown"),
        (200, [], "outcome_unknown"),
        (400, {"error": {"code": 400, "errors": [{"reason": "badRequest"}]}}, "failed"),
        (401, {"error": {"code": 401, "errors": [{"reason": "authError"}]}}, "failed"),
        (403, {"error": {"code": 403, "errors": [{"reason": "domainPolicy"}]}}, "failed"),
        (
            403,
            {"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}},
            "outcome_unknown",
        ),
        (429, {"error": {"code": 429}}, "outcome_unknown"),
        (500, {"error": {"code": 500}}, "outcome_unknown"),
        (302, {}, "outcome_unknown"),
    ],
)
async def test_http_policy_one_post_no_redirect_retry(writes, status, body, state):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://untrusted.invalid/"})

    result = await gmail_sender.send(
        "secret", {"raw": "frozen"}, transport=httpx.MockTransport(handler)
    )
    assert result.state == state and len(requests) == 1
    assert str(requests[0].url) == gmail_sender.SEND_URL
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    "mode", ["timeout", "connect", "invalid_json", "duplicate", "oversize", "wrong_thread"]
)
async def test_uncertain_transport_and_response_never_retries(writes, mode, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE provider content")
        if mode == "connect":
            raise httpx.ConnectError("PRIVATE credentials")
        content = {
            "invalid_json": b"PRIVATE not json",
            "duplicate": b'{"id":"a","id":"b","threadId":"t"}',
            "oversize": b"PRIVATE" * 3000,
            "wrong_thread": b'{"id":"a","threadId":"different"}',
        }[mode]
        return httpx.Response(200, content=content)

    result = await gmail_sender.send(
        "PRIVATE token",
        {"raw": "frozen", "threadId": "target"},
        transport=httpx.MockTransport(handler),
    )
    assert result.state == "outcome_unknown" and len(calls) == 1
    assert "PRIVATE" not in str(result.evidence()) + caplog.text


def test_default_and_b06_code_gate(writes):
    assert Settings(_env_file=None).email_writes_enabled is False
    assert get_settings().email_writes_enabled and not gmail_sender.enabled()
    assert gmail_sender.enabled(httpx.MockTransport(lambda r: httpx.Response(200)))


@needs_pg
async def test_disabled_gate_leaves_approved_job_unclaimed(db_sessionmaker, mailbox, monkeypatch):
    action = await ready(db_sessionmaker, mailbox[0])
    monkeypatch.setattr(get_settings(), "email_writes_enabled", False)
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "approved" and job.state == "queued" and job.attempts == 0
    assert not attempts and not calls


@needs_pg
async def test_corrupt_saved_mime_rejected_even_with_recomputed_payload_hash(
    db_sessionmaker, mailbox
):
    from app.actions import service

    action = await candidate(db_sessionmaker, mailbox[0])
    # Detached object only: persisted bytes are immutable. Exercise both integrity layers.
    action.payload = {**action.payload, "mime_base64url": "aW52YWxpZA=="}
    with pytest.raises(ValueError, match="Invalid saved email payload"):
        gmail_sender.frozen_request(action)
    action.payload_hash = service.candidate_hash(action.payload_schema, action.payload)
    with pytest.raises(ValueError, match="Invalid saved email payload"):
        gmail_sender.frozen_request(action)


@needs_pg
async def test_two_workers_exact_bytes_and_no_post_lock(db_sessionmaker, mailbox, writes):
    action = await ready(db_sessionmaker, mailbox[0])
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "sent-one", "threadId": "thread-one"})

    transport = httpx.MockTransport(handler)
    first = asyncio.create_task(
        worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    )
    await asyncio.wait_for(entered.wait(), 5)
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    # Would deadlock if the provider call held task/action/account row locks.
    await asyncio.wait_for(stop(db_sessionmaker, mailbox[0], action, value=stop_request(3)), 5)
    await asyncio.wait_for(save(db_sessionmaker, mailbox[0], action.task_id), 5)
    async with db_sessionmaker.begin() as session:
        await asyncio.wait_for(session.get(User, mailbox[0], with_for_update=True), 5)
    release.set()
    assert await first
    assert calls == [{"raw": action.payload["mime_base64url"]}]
    assert base64.urlsafe_b64decode(calls[0]["raw"]) == base64.urlsafe_b64decode(
        action.payload["mime_base64url"]
    )
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "succeeded" and job.state == "done" and len(attempts) == 1
    assert saved.result == {"gmail_message_id": "sent-one", "gmail_thread_id": "thread-one"}
    assert saved.payload == action.payload
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
        assert view["cancellation_requested"] and view["result"] == saved.result
        assert not view["sending_available"]


@needs_pg
async def test_preflight_cancel_and_account_change_no_post(db_sessionmaker, mailbox, writes):
    action = await ready(db_sessionmaker, mailbox[0])
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_token(owner):
        entered.set()
        await release.wait()
        return "secret"

    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    running = asyncio.create_task(
        worker.run_once(db_sessionmaker, transport=transport, token_loader=blocked_token)
    )
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.wait_for(stop(db_sessionmaker, mailbox[0], action, value=stop_request(2)), 5)
    release.set()
    await running
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "cancelled" and job.state == "done" and not attempts and not calls


@pytest.mark.parametrize("change", ["account", "scope", "expiry", "write_flag", "edit"])
@needs_pg
async def test_preflight_rechecks_before_intent(
    db_sessionmaker, mailbox, writes, change, monkeypatch
):
    action = await ready(db_sessionmaker, mailbox[0])
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))

    async def mutate(owner):
        async with db_sessionmaker.begin() as session:
            user = await session.get(User, owner)
            if change == "account":
                user.google_account_version += 1
            if change == "scope":
                user.google_scopes = []
            if change == "expiry":
                (await session.get(AssistantAction, action.id)).expires_at = await session.scalar(
                    select(func.clock_timestamp())
                ) - timedelta(seconds=1)
        if change == "write_flag":
            monkeypatch.setattr(get_settings(), "email_writes_enabled", False)
        if change == "edit":
            await save(db_sessionmaker, owner, action.task_id)
        return "secret"

    await worker.run_once(db_sessionmaker, transport=transport, token_loader=mutate)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert not calls and not attempts
    assert saved.state == {"expiry": "expired", "write_flag": "approved"}.get(change, "superseded")


@needs_pg
async def test_crash_before_intent_reclaim_fences_old_claim(db_sessionmaker, mailbox, writes):
    action = await ready(db_sessionmaker, mailbox[0])
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": "m", "threadId": "t"})
    )
    old = await worker.claim_one(db_sessionmaker, transport=transport)
    await expire(db_sessionmaker, action.id)
    new = await worker.claim_one(db_sessionmaker, transport=transport)
    assert new.lease_token != old.lease_token
    assert await worker.prepare(db_sessionmaker, old, transport=transport) is None
    dispatch = await worker.prepare(db_sessionmaker, new, transport=transport)
    assert dispatch is not None
    # Simulated process crash after committed intent, before HTTP: outcome is uncertain.
    await expire(db_sessionmaker, action.id)
    assert await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "outcome_unknown" and attempts[0].state == "outcome_unknown"
    assert job.state == "held" and job.kind == "reconcile"
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)


@needs_pg
async def test_lease_recovery_stale_success_is_evidence_not_state(
    db_sessionmaker, mailbox, writes, monkeypatch
):
    action = await ready(db_sessionmaker, mailbox[0])
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "accepted", "threadId": "t"})

    transport = httpx.MockTransport(handler)
    running = asyncio.create_task(
        worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    )
    await asyncio.wait_for(entered.wait(), 5)
    await expire(db_sessionmaker, action.id)
    monkeypatch.setattr(get_settings(), "email_writes_enabled", False)
    assert await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    release.set()
    await running
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "outcome_unknown" and saved.result is None and count == 1
    assert attempts[0].evidence["late_response"]["message_id"] == "accepted"
    assert job.kind == "reconcile" and job.state == "held"


@needs_pg
async def test_late_observation_survives_subsequent_sweep(db_sessionmaker, mailbox, writes):
    action = await ready(db_sessionmaker, mailbox[0])
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    claim = await worker.claim_one(db_sessionmaker, transport=transport)
    dispatch = await worker.prepare(db_sessionmaker, claim, transport=transport)
    await expire(db_sessionmaker, action.id)
    result = gmail_sender.Outcome("succeeded", "gmail_accepted", "accepted", "t")
    assert not await worker.finish(db_sessionmaker, dispatch, result)
    assert await worker.recover_one(db_sessionmaker)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert attempts[0].evidence["late_response"]["message_id"] == "accepted"
    assert saved.state == "outcome_unknown"


@pytest.mark.parametrize(
    "status,state",
    [
        (401, "failed"),
        (403, "failed"),
        (429, "outcome_unknown"),
        (500, "outcome_unknown"),
        (200, "outcome_unknown"),
    ],
)
@needs_pg
async def test_persisted_error_no_second_send(db_sessionmaker, mailbox, writes, status, state):
    action = await ready(db_sessionmaker, mailbox[0])
    calls = []
    reason = {401: "authError", 403: "domainPolicy"}.get(status, "unknown")
    transport = httpx.MockTransport(
        lambda r: (
            calls.append(r)
            or httpx.Response(
                status,
                json={
                    "error": {"code": status, "errors": [{"reason": reason}], "message": "PRIVATE"}
                },
            )
        )
    )
    await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == state and attempts[0].state == state
    assert job.state == ("done" if state == "failed" else "held")
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert len(calls) == 1 and "PRIVATE" not in str(attempts[0].evidence)


@needs_pg
async def test_timeout_after_acceptance_and_preflight_budget(db_sessionmaker, mailbox, writes):
    action = await ready(db_sessionmaker, mailbox[0])
    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def unavailable(owner):
        raise ApiError(503, "google_token_unavailable", "PRIVATE")

    for _ in range(3):
        await worker.run_once(db_sessionmaker, transport=transport, token_loader=unavailable)
        async with db_sessionmaker.begin() as session:
            (await session.get(ActionJob, action.id)).available_at = await session.scalar(
                select(func.clock_timestamp())
            ) - timedelta(seconds=1)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "approved" and job.state == "held" and job.attempts == 3 and not attempts
    async with db_sessionmaker() as session:
        assert "PRIVATE" not in str(
            [e.payload for e in (await session.scalars(select(TaskEvent))).all()]
        )


@pytest.mark.parametrize("mode", ["timeout", "process_cancel"])
@needs_pg
async def test_accepted_but_response_lost_never_resends(db_sessionmaker, mailbox, writes, mode):
    action = await ready(db_sessionmaker, mailbox[0])
    accepted = []
    entered, forever = asyncio.Event(), asyncio.Event()

    async def handler(request):
        accepted.append(json.loads(request.content))
        entered.set()
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE accepted but response lost")
        await forever.wait()

    transport = httpx.MockTransport(handler)
    running = asyncio.create_task(
        worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    )
    await asyncio.wait_for(entered.wait(), 5)
    if mode == "process_cancel":
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        await expire(db_sessionmaker, action.id)
        await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    else:
        await running
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "outcome_unknown" and len(accepted) == 1 and len(attempts) == 1
    assert job.kind == "reconcile" and job.state == "held"
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)


@needs_pg
async def test_result_commit_failure_preserves_intent(db_sessionmaker, mailbox, writes):
    from sqlalchemy import event

    action = await ready(db_sessionmaker, mailbox[0])
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={"id": "m", "threadId": "t"})
    )

    def fail_result(mapper, connection, target):
        if target.state == "succeeded":
            raise RuntimeError("simulated database interruption")

    event.listen(AssistantAction, "before_update", fail_result)
    try:
        with pytest.raises(RuntimeError):
            await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    finally:
        event.remove(AssistantAction, "before_update", fail_result)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "executing" and attempts[0].state == "dispatched"
    await expire(db_sessionmaker, action.id)
    await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert len(calls) == 1


@needs_pg
async def test_reply_uses_saved_thread_and_headers(db_sessionmaker, mailbox, writes):
    from sqlalchemy import update

    from app.db.models import Message
    from tests.test_draft_review import generated
    from tests.test_email_previews import accept, connect

    await connect(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        (await session.get(User, mailbox[0])).google_scopes = [
            "https://www.googleapis.com/auth/gmail.modify"
        ]
    draft = await generated(db_sessionmaker, mailbox[0], reply=True)
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(Message).values(
                reply_metadata={
                    "schema_version": "1.0",
                    "headers": {
                        "message-id": ["<message@example.test>"],
                        "references": [],
                        "in-reply-to": [],
                    },
                }
            )
        )
    action = await accept(db_sessionmaker, mailbox[0], draft)
    await approve(db_sessionmaker, mailbox[0], action)
    requests = []
    transport = httpx.MockTransport(
        lambda r: (
            requests.append(json.loads(r.content))
            or httpx.Response(200, json={"id": "reply-sent", "threadId": "thread-one"})
        )
    )
    await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert requests == [{"raw": action.payload["mime_base64url"], "threadId": "thread-one"}]
    assert b"In-Reply-To: <message@example.test>" in base64.urlsafe_b64decode(requests[0]["raw"])


@needs_pg
async def test_pre_intent_insert_failure_rolls_back_and_can_reclaim(
    db_sessionmaker, mailbox, writes
):
    from sqlalchemy import event

    action = await ready(db_sessionmaker, mailbox[0])
    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={"id": "m", "threadId": "t"})
    )

    def fail_insert(mapper, connection, target):
        raise RuntimeError("simulated pre-intent crash")

    event.listen(ActionAttempt, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError):
            await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    finally:
        event.remove(ActionAttempt, "before_insert", fail_insert)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "approved" and not attempts and not calls
    await expire(db_sessionmaker, action.id)
    await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert len(calls) == 1
