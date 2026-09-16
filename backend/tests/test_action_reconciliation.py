"""Recovery durability, account fencing, ownership and no resend across failures."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, func, select

from app.actions import email_preview, gmail_sender, reconciliation, worker
from app.config import get_settings
from app.db.models import ActionJob, AssistantAction, User
from tests.conftest import needs_pg
from tests.test_action_approval import stop, stop_request
from tests.test_draft_review import save
from tests.test_durable_tasks import mailbox
from tests.test_gmail_action_worker import expire, read, ready, token
from tests.test_gmail_reconciliation import Sent

__all__ = ["mailbox"]


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_writes_enabled", True)
    monkeypatch.setattr(get_settings(), "email_reconciliation_enabled", True)


async def unknown(factory, owner):
    action = await ready(factory, owner)
    calls = []

    def accept_lost(request):
        calls.append(request)
        raise httpx.ReadTimeout("accepted but lost")

    await worker.run_once(factory, transport=httpx.MockTransport(accept_lost), token_loader=token)
    action, _, attempts = await read(factory, action.id)
    assert action.state == "outcome_unknown" and len(calls) == 1
    return action, attempts[0], calls


async def due(factory, action_id):
    async with factory.begin() as session:
        job = await session.get(ActionJob, action_id)
        job.available_at = await session.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=1
        )


@needs_pg
async def test_lost_send_reconciles_once_with_writes_off(
    db_sessionmaker, mailbox, enabled, monkeypatch
):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    monkeypatch.setattr(get_settings(), "email_writes_enabled", False)
    sent = Sent(action.payload, attempt.dispatch_intent_at)
    transport = httpx.MockTransport(sent)
    assert await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert not await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    current, job, attempts = await read(db_sessionmaker, action.id)
    assert current.state == "succeeded" and current.result["gmail_message_id"] == "sent-one"
    assert (
        attempts[0].state == "succeeded"
        and attempts[0].evidence["resolution"]["code"] == "sent_evidence_matched"
    )
    assert job.state == "done" and job.kind == "reconcile" and len(posts) == 1
    assert len(sent.calls) == 3


@needs_pg
async def test_empty_budget_is_persisted_manual_not_failed(db_sessionmaker, mailbox, enabled):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    sent = Sent(action.payload, attempt.dispatch_intent_at, pages=[{}])
    transport = httpx.MockTransport(sent)
    for n in range(1, 4):
        assert await reconciliation.run_once(
            db_sessionmaker, transport=transport, token_loader=token
        )
        saved, job, attempts = await read(db_sessionmaker, action.id)
        assert (
            saved.state == "outcome_unknown"
            and attempts[0].evidence["reconciliation"]["rounds"] == n
        )
        assert not await reconciliation.run_once(
            db_sessionmaker, transport=transport, token_loader=token
        )
        if n < 3:
            assert job.state == "queued"
            await due(db_sessionmaker, action.id)
    assert job.state == "held" and len(posts) == 1 and len(sent.calls) == 6
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
    assert view["recovery"]["status"] == "manual_inspection"
    assert view["recovery"]["rounds"] == 3 and view["recovery"]["next_check_at"] is None
    assert len(attempts[0].evidence["reconciliation"]["observations"]) == 3


@needs_pg
async def test_delayed_indexing_retry_reads_only(db_sessionmaker, mailbox, enabled):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    sent = Sent(action.payload, attempt.dispatch_intent_at, pages=[{}])
    transport = httpx.MockTransport(sent)
    await reconciliation.run_once(db_sessionmaker, transport=transport, token_loader=token)
    sent.pages = [{"messages": [{"id": "sent-one", "threadId": "thread-one"}]}]
    await due(db_sessionmaker, action.id)
    await reconciliation.run_once(db_sessionmaker, transport=transport, token_loader=token)
    saved, _, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "succeeded" and len(posts) == 1
    assert [v["code"] for v in attempts[0].evidence["reconciliation"]["observations"]] == [
        "not_observed",
        "sent_evidence_matched",
    ]


@pytest.mark.parametrize("change", ["scope", "disconnect", "account", "identity"])
@needs_pg
async def test_missing_access_before_token_has_no_egress(db_sessionmaker, mailbox, enabled, change):
    action, _, posts = await unknown(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, mailbox[0])
        if change == "scope":
            user.google_scopes = ["https://www.googleapis.com/auth/gmail.metadata"]
        if change == "disconnect":
            user.google_connected = False
        if change == "account":
            user.google_sub = "other-account"
        if change == "identity":
            user.google_identity = {"sub": "mismatch"}

    async def forbidden(owner):
        pytest.fail("must not load token for wrong account or missing scope")

    await reconciliation.run_once(db_sessionmaker, token_loader=forbidden)
    current, _, _ = await read(db_sessionmaker, action.id)
    assert (
        current.state == "outcome_unknown" and current.error_code == "recovery_access_unavailable"
    )
    assert len(posts) == 1


@needs_pg
async def test_reconnect_same_subject_can_recover_with_readonly_grant(
    db_sessionmaker, mailbox, enabled
):
    action, attempt, _ = await unknown(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, mailbox[0])
        user.google_account_version += 1
        user.google_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    sent = Sent(action.payload, attempt.dispatch_intent_at)
    await reconciliation.run_once(
        db_sessionmaker, transport=httpx.MockTransport(sent), token_loader=token
    )
    assert (await read(db_sessionmaker, action.id))[0].state == "succeeded"


@needs_pg
async def test_two_readers_no_locks_and_account_change_fences_result(
    db_sessionmaker, mailbox, enabled
):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    entered, release = asyncio.Event(), asyncio.Event()
    sent = Sent(action.payload, attempt.dispatch_intent_at)

    async def blocked(request):
        if request.url.path.endswith("/messages/sent-one"):
            entered.set()
            await release.wait()
        return sent(request)

    transport = httpx.MockTransport(blocked)
    first = asyncio.create_task(
        reconciliation.run_once(db_sessionmaker, transport=transport, token_loader=token)
    )
    await asyncio.wait_for(entered.wait(), 5)
    assert not await reconciliation.run_once(
        db_sessionmaker, transport=transport, token_loader=token
    )
    await asyncio.wait_for(
        stop(db_sessionmaker, mailbox[0], action, value=stop_request(action.version)), 5
    )
    await asyncio.wait_for(save(db_sessionmaker, mailbox[0], action.task_id), 5)
    async with db_sessionmaker.begin() as session:
        user = await asyncio.wait_for(session.get(User, mailbox[0], with_for_update=True), 5)
        user.google_account_version += 1
    release.set()
    await first
    saved, _, _ = await read(db_sessionmaker, action.id)
    assert saved.state == "outcome_unknown" and saved.error_code == "account_changed_during_read"
    assert len(posts) == 1


@needs_pg
async def test_read_crashes_exhaust_budget_and_old_completion_fenced(
    db_sessionmaker, mailbox, enabled
):
    action, _, posts = await unknown(db_sessionmaker, mailbox[0])
    old = None
    for _ in range(3):
        claim = await reconciliation.claim_one(db_sessionmaker)
        old = old or claim
        assert claim
        await expire(db_sessionmaker, action.id)
    assert await reconciliation.claim_one(db_sessionmaker) is None
    assert not await reconciliation.finish(
        db_sessionmaker,
        old,
        gmail_sender.Outcome("succeeded", "sent_evidence_matched", "late", "t"),
        1,
    )
    current, job, attempts = await read(db_sessionmaker, action.id)
    assert current.state == "outcome_unknown" and job.state == "held" and len(posts) == 1
    assert attempts[0].evidence["reconciliation"]["rounds"] == 3
    assert all(
        o["code"] == "read_lease_expired"
        for o in attempts[0].evidence["reconciliation"]["observations"]
    )


@needs_pg
async def test_result_commit_failure_read_repeats_without_send(db_sessionmaker, mailbox, enabled):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    sent = Sent(action.payload, attempt.dispatch_intent_at)

    def fail_commit(mapper, connection, target):
        if target.state == "succeeded":
            raise RuntimeError("simulated commit failure")

    event.listen(AssistantAction, "before_update", fail_commit)
    try:
        with pytest.raises(RuntimeError):
            await reconciliation.run_once(
                db_sessionmaker, transport=httpx.MockTransport(sent), token_loader=token
            )
    finally:
        event.remove(AssistantAction, "before_update", fail_commit)
    saved, job, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == attempts[0].state == "outcome_unknown" and job.state == "running"
    await expire(db_sessionmaker, action.id)
    await reconciliation.run_once(
        db_sessionmaker, transport=httpx.MockTransport(sent), token_loader=token
    )
    assert (await read(db_sessionmaker, action.id))[0].state == "succeeded" and len(posts) == 1


@needs_pg
async def test_recovery_view_owned_and_flag_pause(
    db_sessionmaker, db_client, auth_headers, mailbox, enabled, monkeypatch
):
    action, _, _ = await unknown(db_sessionmaker, mailbox[0])
    monkeypatch.setattr(get_settings(), "email_reconciliation_enabled", False)
    assert not await reconciliation.run_once(db_sessionmaker)
    response = db_client.get(f"/assistant/actions/{action.id}", headers=auth_headers(mailbox[0]))
    assert response.status_code == 200 and response.json()["recovery"]["status"] == "paused"
    assert response.headers["cache-control"] == "no-store"
    response = db_client.get(f"/assistant/actions/{action.id}", headers=auth_headers(mailbox[1]))
    assert response.status_code == 404


@needs_pg
async def test_edit_and_late_cancel_do_not_erase_historical_sent_evidence(
    db_sessionmaker, mailbox, enabled
):
    action, attempt, posts = await unknown(db_sessionmaker, mailbox[0])
    await stop(db_sessionmaker, mailbox[0], action, value=stop_request(action.version))
    await save(db_sessionmaker, mailbox[0], action.task_id)
    sent = Sent(action.payload, attempt.dispatch_intent_at)
    await reconciliation.run_once(
        db_sessionmaker, transport=httpx.MockTransport(sent), token_loader=token
    )
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
    assert (
        view["state"] == "succeeded" and view["cancellation_requested"] and view["recovery"] is None
    )
    assert len(posts) == 1


@needs_pg
async def test_conflicting_late_response_during_read_is_not_overwritten(
    db_sessionmaker, mailbox, enabled
):
    from app.db.models import ActionAttempt

    action, attempt, _ = await unknown(db_sessionmaker, mailbox[0])
    sent = Sent(action.payload, attempt.dispatch_intent_at)

    async def late(request):
        if request.url.path.endswith("/messages/sent-one"):
            async with db_sessionmaker.begin() as session:
                saved = await session.get(ActionAttempt, attempt.id)
                saved.evidence = {
                    **saved.evidence,
                    "late_response": {"state": "failed", "code": "gmail_rejected_401"},
                }
        return sent(request)

    await reconciliation.run_once(
        db_sessionmaker, transport=httpx.MockTransport(late), token_loader=token
    )
    saved, _, attempts = await read(db_sessionmaker, action.id)
    assert saved.state == "outcome_unknown" and saved.error_code == "conflicting_late_response"
    assert attempts[0].evidence["late_response"]["state"] == "failed"


@needs_pg
async def test_scope_revocation_during_token_refresh_stops_mail_reads(
    db_sessionmaker, mailbox, enabled
):
    action, _, _ = await unknown(db_sessionmaker, mailbox[0])

    async def revoke(owner):
        async with db_sessionmaker.begin() as session:
            (await session.get(User, owner)).google_scopes = []
        return "token"

    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
    await reconciliation.run_once(db_sessionmaker, transport=transport, token_loader=revoke)
    assert not calls and (await read(db_sessionmaker, action.id))[0].state == "outcome_unknown"


@needs_pg
async def test_unknown_blocks_new_proposal_even_after_edit_but_original_replays(
    db_sessionmaker, mailbox, enabled
):
    from app.api.errors import ApiError
    from tests.test_email_previews import accept, request

    action, _, _ = await unknown(db_sessionmaker, mailbox[0])
    from app.db.models import ArtifactRevision

    async with db_sessionmaker() as session:
        artifact = await session.get(ArtifactRevision, action.artifact_id)
    replay = await accept(db_sessionmaker, mailbox[0], artifact)
    assert replay.id == action.id
    edited = await save(db_sessionmaker, mailbox[0], action.task_id)
    with pytest.raises(ApiError) as error:
        await accept(db_sessionmaker, mailbox[0], edited, request("new-key", revision=2))
    assert error.value.detail["blockers"] == ["previous_send_unresolved"]


@needs_pg
async def test_two_preapproved_actions_same_task_cannot_bypass_unknown(
    db_sessionmaker, mailbox, enabled, monkeypatch
):
    from app.db.models import ArtifactRevision
    from tests.test_action_approval import approve, approve_request
    from tests.test_email_previews import accept, request

    first = await ready(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        artifact = await session.get(ArtifactRevision, first.artifact_id)
    second = await accept(db_sessionmaker, mailbox[0], artifact, request("second"))
    await approve(db_sessionmaker, mailbox[0], second, approve_request(second, request_id="second"))
    monkeypatch.setattr(get_settings(), "email_reconciliation_enabled", False)
    posts = []

    def lost(r):
        posts.append(r)
        raise httpx.ReadTimeout("uncertain")

    transport = httpx.MockTransport(lost)
    assert await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    assert await worker.run_once(db_sessionmaker, transport=transport, token_loader=token)
    states = [(await read(db_sessionmaker, a.id))[0].state for a in (first, second)]
    assert sorted(states) == ["outcome_unknown", "superseded"] and len(posts) == 1


@needs_pg
async def test_definite_rejection_requires_fresh_unapproved_candidate(
    db_sessionmaker, mailbox, enabled
):
    from app.db.models import ArtifactRevision
    from tests.test_email_previews import accept, request

    action = await ready(db_sessionmaker, mailbox[0])
    posts = []

    def reject(r):
        posts.append(r)
        return httpx.Response(
            401, json={"error": {"code": 401, "errors": [{"reason": "authError"}]}}
        )

    await worker.run_once(
        db_sessionmaker, transport=httpx.MockTransport(reject), token_loader=token
    )
    failed, _, _ = await read(db_sessionmaker, action.id)
    assert failed.state == "failed"
    async with db_sessionmaker() as session:
        artifact = await session.get(ArtifactRevision, action.artifact_id)
    new = await accept(db_sessionmaker, mailbox[0], artifact, request("review-again"))
    assert (
        new.id != action.id and new.payload_hash != action.payload_hash and new.state == "proposed"
    )
    async with db_sessionmaker() as session:
        assert await session.get(ActionJob, new.id) is None
    assert not await worker.run_once(
        db_sessionmaker, transport=httpx.MockTransport(reject), token_loader=token
    )
    assert len(posts) == 1
