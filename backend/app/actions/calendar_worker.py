"""Isolated Calendar dispatch/recovery with one durable insert attempt per approval."""

from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import and_, func, or_, select

from app.actions import calendar_executor as executor
from app.actions import calendar_preview as preview
from app.actions import service, worker
from app.api.errors import ApiError
from app.auth.service import get_valid_access_token
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionJob,
    AssistantAction,
    AssistantTask,
    CalendarPreference,
    User,
)


async def claim_one(factory, *, transport=None):
    if not get_settings().calendar_writes_enabled:
        return None
    async with factory.begin() as session:
        action = await worker.candidate(
            session,
            action_type="create_event",
            eligible_owners=None
            if isinstance(transport, httpx.MockTransport)
            else [int(v) for v in get_settings().write_pilot_user_ids_values],
        )
        if action is None or not executor.enabled(action.user_id, transport):
            return None
        claim = worker.Claim(action.id, action.user_id, str(uuid4()))
        task, action, job = await worker.locked(session, claim)
        if job.attempts >= 3 or await session.scalar(
            select(ActionAttempt.id).where(ActionAttempt.action_id == action.id).limit(1)
        ):
            worker.close_job(job, "held")
            return None
        job.state, job.lease_token = "running", claim.lease_token
        job.lease_expires_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=120
        )
        job.attempts += 1
        return claim


async def prepare(factory, claim, *, transport=None, dispatch=False, preflight_code=None):
    async with factory.begin() as session:
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
        if not executor.enabled(claim.user_id, transport):
            worker.close_job(job, "queued")
            return None
        reasons = await preview.blockers(session, claim.user_id, action)
        consent = await session.get(ActionApproval, job.approval_id)
        if (
            consent is None
            or consent.action_id != action.id
            or consent.user_id != claim.user_id
            or consent.payload_hash != action.payload_hash
            or consent.action_version != action.version - 1
            or consent.expires_at <= now
        ):
            reasons.append("approval_changed_or_expired")
        if preflight_code:
            reasons.append(preflight_code)
        try:
            payload = executor.frozen(action)
        except ValueError:
            reasons.append("payload_integrity_failed")
        if reasons:
            await service.stop_before_dispatch(
                session,
                claim.user_id,
                action.id,
                expected_version=action.version,
                state="expired" if "action_expired" in reasons else "superseded",
            )
            worker.event(session, task, action, "action.preflight_blocked", code=reasons[0])
            return None
        pref = await session.get(CalendarPreference, claim.user_id)
        if not dispatch:
            return payload, pref.preferences
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
                    "calendar_id": payload["calendar_id"],
                    "event_id": payload["event"]["id"],
                    "payload_hash": action.payload_hash,
                },
            )
        )
        await session.flush()
        action.state, action.version = "executing", action.version + 1
        worker.event(session, task, action, "action.state_changed", state=action.state)
        return worker.Dispatch(claim, attempt_id, action.version, payload)


async def finish(factory, dispatch, outcome, *, reconciliation=False):
    async with factory.begin() as session:
        task, action, job = await worker.locked(session, dispatch.claim)
        attempt = await session.get(
            ActionAttempt, dispatch.attempt_id, with_for_update=True, populate_existing=True
        )
        now = await session.scalar(select(func.clock_timestamp()))
        expected_state = "outcome_unknown" if reconciliation else "executing"
        if (
            attempt is None
            or action.state != expected_state
            or action.version != dispatch.action_version
            or job.state != "running"
            or job.lease_token != dispatch.claim.lease_token
            or job.lease_expires_at <= now
        ):
            if attempt and attempt.state in {"dispatched", "outcome_unknown"}:
                attempt.evidence = {**(attempt.evidence or {}), "late_response": outcome.evidence()}
            return False
        attempt.evidence = {**(attempt.evidence or {}), **outcome.evidence()}
        if reconciliation and outcome.state == "outcome_unknown":
            worker.close_job(job, "held", "reconcile")
            job.available_at = now + timedelta(seconds=30)
            worker.event(session, task, action, "action.reconciliation_required", code=outcome.code)
            return True
        attempt.state = outcome.state
        await session.flush()
        action.state, action.version = outcome.state, action.version + 1
        action.error_code = None if outcome.state == "succeeded" else outcome.code
        action.result = (
            {"calendar_id": action.payload["calendar_id"], "event_id": outcome.event_id}
            if outcome.state == "succeeded"
            else None
        )
        worker.close_job(
            job,
            "held" if outcome.state == "outcome_unknown" else "done",
            "reconcile" if outcome.state == "outcome_unknown" else "dispatch",
        )
        worker.event(session, task, action, "action.state_changed", state=action.state)
        return True


async def token_for(factory, owner, loader):
    if loader:
        return await loader(owner)
    async with factory() as session:
        return await get_valid_access_token(session, User(id=owner))


async def reconcile_one(factory, *, transport=None, token_loader=None):
    if not get_settings().calendar_reconciliation_enabled:
        return False
    async with factory.begin() as session:
        now = func.clock_timestamp()
        rounds = func.coalesce(ActionAttempt.evidence["calendar_read_rounds"].as_integer(), 0)
        action = await session.scalar(
            select(AssistantAction)
            .join(AssistantTask, AssistantTask.id == AssistantAction.task_id)
            .join(ActionJob, ActionJob.action_id == AssistantAction.id)
            .join(ActionAttempt, ActionAttempt.action_id == AssistantAction.id)
            .where(
                AssistantAction.action_type == "create_event",
                AssistantAction.state == "outcome_unknown",
                ActionAttempt.state == "outcome_unknown",
                ActionJob.kind == "reconcile",
                rounds < 3,
                or_(
                    and_(ActionJob.state.in_(["held", "queued"]), ActionJob.available_at <= now),
                    and_(ActionJob.state == "running", ActionJob.lease_expires_at <= now),
                ),
            )
            .order_by(ActionJob.available_at)
            .with_for_update(of=AssistantTask, skip_locked=True)
            .limit(1)
        )
        if action is None:
            return False
        claim = worker.Claim(action.id, action.user_id, str(uuid4()))
        task, action, job = await worker.locked(session, claim)
        await session.get(User, claim.user_id, with_for_update=True, populate_existing=True)
        attempt = await session.scalar(
            select(ActionAttempt)
            .where(ActionAttempt.action_id == action.id, ActionAttempt.state == "outcome_unknown")
            .with_for_update()
        )
        attempt.evidence = {
            **(attempt.evidence or {}),
            "calendar_read_rounds": (attempt.evidence or {}).get("calendar_read_rounds", 0) + 1,
        }
        job.state, job.lease_token = "running", claim.lease_token
        job.lease_expires_at = await session.scalar(select(func.clock_timestamp())) + timedelta(
            seconds=120
        )
        dispatch = worker.Dispatch(claim, attempt.id, action.version, executor.frozen(action))
        expected_identity = action.source_versions["google_subject"]
    try:
        token = await token_for(factory, claim.user_id, token_loader)
        async with factory() as session:
            user = await preview.ready_account(session, claim.user_id)
            if user.google_sub != expected_identity:
                raise ApiError(409, "calendar_account_changed", "Reconnect the original account.")
        outcome = await executor.request_event(
            token, dispatch.request, read=True, transport=transport
        )
    except Exception:
        outcome = executor.Outcome("outcome_unknown", "calendar_reconciliation_unavailable")
    await finish(factory, dispatch, outcome, reconciliation=True)
    return True


async def run_once(factory=None, *, transport=None, token_loader=None):
    factory = factory or get_session_factory()
    if await worker.recover_one(factory, action_type="create_event"):
        return True
    if await reconcile_one(factory, transport=transport, token_loader=token_loader):
        return True
    claim = await claim_one(factory, transport=transport)
    if claim is None:
        return False
    prepared = await prepare(factory, claim, transport=transport)
    if prepared is None:
        return True
    payload, prefs = prepared
    try:
        token = await token_for(factory, claim.user_id, token_loader)
        code = await executor.preflight(token, payload, prefs, transport=transport)
    except ApiError as error:
        await worker.preflight_error(factory, claim, error.code)
        return True
    dispatch = await prepare(
        factory, claim, transport=transport, dispatch=True, preflight_code=code
    )
    if dispatch is None:
        return True
    # Check rollout again after committing intent. No inferred write permission.
    if not executor.enabled(claim.user_id, transport):
        outcome = executor.Outcome("failed", "calendar_writes_disabled_before_http")
    else:
        try:
            outcome = await executor.request_event(
                token, dispatch.request, transport=transport, owner=claim.user_id
            )
        except Exception:
            outcome = executor.Outcome("outcome_unknown", "calendar_dispatch_interrupted")
    await finish(factory, dispatch, outcome)
    return True
