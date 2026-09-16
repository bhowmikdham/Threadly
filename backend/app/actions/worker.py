"""Isolated email dispatch lifecycle. No generation jobs or blind write retries."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select

from app.actions import email_preview, gmail_sender, service
from app.api.errors import ApiError
from app.assistant import tasks
from app.auth.service import get_valid_access_token
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionJob,
    AssistantAction,
    AssistantTask,
    User,
)


@dataclass(frozen=True)
class Claim:
    action_id: str
    user_id: int
    lease_token: str


@dataclass(frozen=True)
class Dispatch:
    claim: Claim
    attempt_id: str
    action_version: int
    request: dict


async def candidate(session, *, recovering=False):
    now = func.clock_timestamp()
    expired = and_(ActionJob.state == "running", ActionJob.lease_expires_at <= now)
    predicate = (
        and_(AssistantAction.state == "executing", expired)
        if recovering
        else and_(
            AssistantAction.state == "approved",
            or_(and_(ActionJob.state == "queued", ActionJob.available_at <= now), expired),
        )
    )
    # Lock the task first; never lock a job then wait for its task/action.
    return await session.scalar(
        select(AssistantAction)
        .join(AssistantTask, AssistantTask.id == AssistantAction.task_id)
        .join(ActionJob, ActionJob.action_id == AssistantAction.id)
        .where(ActionJob.kind == "dispatch", predicate)
        .order_by(ActionJob.available_at, AssistantAction.id)
        .with_for_update(of=AssistantTask, skip_locked=True)
        .limit(1)
    )


async def locked(session, identity):
    task, action = await service.owned_action(
        session, identity.user_id, identity.action_id, lock=True
    )
    job = await session.get(ActionJob, action.id, with_for_update=True, populate_existing=True)
    return task, action, job


def event(session, task, action, kind, **extra):
    task.version += 1
    tasks.add_event(session, task, kind, {"action_id": action.id, **extra})


def close_job(job, state="done", kind="dispatch"):
    job.state, job.kind = state, kind
    job.lease_token, job.lease_expires_at = None, None


async def recover_one(factory):
    async with factory.begin() as session:
        candidate_action = await candidate(session, recovering=True)
        if candidate_action is None:
            return False
        identity = Claim(candidate_action.id, candidate_action.user_id, "")
        task, action, job = await locked(session, identity)
        now = await session.scalar(select(func.clock_timestamp()))
        if action.state != "executing" or job.state != "running" or job.lease_expires_at > now:
            return False
        attempt = await session.scalar(
            select(ActionAttempt)
            .where(ActionAttempt.action_id == action.id, ActionAttempt.state == "dispatched")
            .with_for_update()
        )
        if attempt is None:
            # Corrupt/incompatible records cannot justify a send or fabricated result.
            close_job(job, "held", "reconcile")
            event(session, task, action, "action.recovery_required", code="attempt_missing")
            return True
        attempt.state, attempt.evidence = (
            "outcome_unknown",
            {**(attempt.evidence or {}), "code": "dispatch_lease_expired"},
        )
        await session.flush()
        action.state, action.version, action.error_code = (
            "outcome_unknown",
            action.version + 1,
            "dispatch_lease_expired",
        )
        close_job(job, "held", "reconcile")
        event(session, task, action, "action.state_changed", state=action.state)
        return True


async def claim_one(factory, *, transport=None):
    if not gmail_sender.enabled(transport):
        return None
    async with factory.begin() as session:
        action = await candidate(session)
        if action is None:
            return None
        identity = Claim(action.id, action.user_id, str(uuid4()))
        task, action, job = await locked(session, identity)
        if job.attempts >= 3 or await session.scalar(
            select(ActionAttempt.id).where(ActionAttempt.action_id == action.id).limit(1)
        ):
            close_job(job, "held")
            event(
                session,
                task,
                action,
                "action.preflight_blocked",
                code="preflight_budget_or_attempt",
            )
            return None
        job.state, job.lease_token = "running", identity.lease_token
        job.lease_expires_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=get_settings().email_action_lease_seconds
        )
        job.attempts += 1
        return identity


async def prepare(factory, claim, *, transport=None):
    async with factory.begin() as session:
        # Account precedes job in the approval/dispatch lock order.
        task, action = await service.owned_action(
            session, claim.user_id, claim.action_id, lock=True
        )
        await session.get(User, claim.user_id, with_for_update=True, populate_existing=True)
        job = await session.get(ActionJob, action.id, with_for_update=True, populate_existing=True)
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            action.state != "approved"
            or job.state != "running"
            or job.kind != "dispatch"
            or job.lease_token != claim.lease_token
            or job.lease_expires_at <= now
        ):
            return None
        if not gmail_sender.enabled(transport):
            close_job(job, "queued")
            return None
        view = await email_preview.view(session, claim.user_id, action.id)
        blockers = [
            b for b in view["blockers"] if b not in {"action_approved", "send_executor_unavailable"}
        ]
        consent = await session.get(ActionApproval, job.approval_id)
        if (
            consent is None
            or consent.user_id != claim.user_id
            or consent.action_id != action.id
            or consent.payload_hash != action.payload_hash
            or consent.action_version != action.version - 1
            or consent.expires_at <= now
        ):
            blockers.append("approval_changed_or_expired")
        try:
            request = gmail_sender.frozen_request(action)
        except ValueError:
            blockers.append("payload_integrity_failed")
        if blockers:
            target = "expired" if "action_expired" in blockers else "superseded"
            await service.stop_before_dispatch(
                session, claim.user_id, action.id, expected_version=action.version, state=target
            )
            event(
                session,
                task,
                action,
                "action.preflight_blocked",
                code="action_preconditions_changed",
            )
            return None
        attempt_id = str(uuid4())
        session.add(
            ActionAttempt(
                id=attempt_id,
                user_id=claim.user_id,
                action_id=action.id,
                approval_id=consent.id,
                number=1,
                action_version=action.version,
                lease_token=claim.lease_token,
                state="dispatched",
                dispatch_intent_at=now,
                provider_identifiers={
                    "message_id": action.payload["preview"]["message_id"],
                    "gmail_thread_id": request.get("threadId"),
                    "payload_hash": action.payload_hash,
                },
            )
        )
        await session.flush()
        action.state, action.version = "executing", action.version + 1
        event(session, task, action, "action.state_changed", state="executing")
        return Dispatch(claim, attempt_id, action.version, request)


async def finish(factory, dispatch, outcome):
    async with factory.begin() as session:
        task, action, job = await locked(session, dispatch.claim)
        attempt = await session.get(
            ActionAttempt, dispatch.attempt_id, with_for_update=True, populate_existing=True
        )
        if (
            attempt is None
            or attempt.action_id != action.id
            or attempt.lease_token != dispatch.claim.lease_token
        ):
            return False
        now = await session.scalar(select(func.clock_timestamp()))
        current = (
            action.state == "executing"
            and action.version == dispatch.action_version
            and job.state == "running"
            and job.lease_token == dispatch.claim.lease_token
            and job.lease_expires_at > now
            and attempt.state == "dispatched"
        )
        if not current:
            # One bounded late observation survives for B06, never overwrites a
            # terminal/reconciled outcome and never re-enqueues a dispatch.
            if attempt.state in {"dispatched", "outcome_unknown"} and not (
                attempt.evidence or {}
            ).get("late_response"):
                attempt.evidence = {**(attempt.evidence or {}), "late_response": outcome.evidence()}
                event(session, task, action, "action.late_response", attempt_id=attempt.id)
            return False
        attempt.state, attempt.evidence = outcome.state, outcome.evidence()
        await session.flush()
        action.state, action.version = outcome.state, action.version + 1
        action.error_code = None if outcome.state == "succeeded" else outcome.code
        action.result = (
            {"gmail_message_id": outcome.message_id, "gmail_thread_id": outcome.thread_id}
            if outcome.state == "succeeded"
            else None
        )
        close_job(
            job,
            "held" if outcome.state == "outcome_unknown" else "done",
            "reconcile" if outcome.state == "outcome_unknown" else "dispatch",
        )
        event(session, task, action, "action.state_changed", state=action.state)
        return True


async def preflight_error(factory, claim, code):
    async with factory.begin() as session:
        task, action, job = await locked(session, claim)
        if (
            action.state != "approved"
            or job.state != "running"
            or job.lease_token != claim.lease_token
        ):
            return
        retry = code == "google_token_unavailable" and job.attempts < 3
        close_job(job, "queued" if retry else "held")
        job.available_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=30
        )
        event(session, task, action, "action.preflight_blocked", code=code)


async def run_once(factory=None, *, transport=None, token_loader=None):
    factory = factory or get_session_factory()
    if await recover_one(factory):
        return True
    from app.actions import reconciliation

    if await reconciliation.run_once(factory, transport=transport, token_loader=token_loader):
        return True
    claim = await claim_one(factory, transport=transport)
    if claim is None:
        return False
    try:
        if token_loader:
            token = await token_loader(claim.user_id)
        else:
            # This session has no transaction/row reads; auth owns its separate
            # credential transactions and network call. Only the owned ID is passed.
            async with factory() as session:
                token = await get_valid_access_token(session, User(id=claim.user_id))
    except ApiError as error:
        code = (
            error.code
            if error.code
            in {"reauth_required", "google_token_unavailable", "google_connection_changed"}
            else "credential_unavailable"
        )
        await preflight_error(factory, claim, code)
        return True
    dispatch = await prepare(factory, claim, transport=transport)
    if dispatch is None:
        return True
    try:
        outcome = await gmail_sender.send(token, dispatch.request, transport=transport)
    except Exception:
        # Never expose exception/provider text or retry a possibly dispatched call.
        outcome = gmail_sender.Outcome("outcome_unknown", "dispatch_interrupted")
    await finish(factory, dispatch, outcome)
    return True


async def main():
    while True:
        try:
            worked = await run_once()
        except Exception:
            # Failed database result persistence leaves intent/lease for recovery.
            logging.getLogger(__name__).warning(
                "Action worker iteration failed; recovery records preserved"
            )
            worked = False
        if not worked:
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
