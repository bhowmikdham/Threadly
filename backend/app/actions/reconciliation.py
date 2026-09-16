"""Durable read-only recovery rounds, independent of write enablement and draft edits."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select

from app.actions import gmail_reconciliation, gmail_sender, service, worker
from app.api.errors import ApiError
from app.auth.service import get_valid_access_token
from app.capabilities.service import GMAIL_READ_SCOPES
from app.config import get_settings
from app.db.models import ActionAttempt, ActionJob, AssistantAction, AssistantTask, User

MAX_ROUNDS = 3
DELAYS = (30, 120, 600)


@dataclass(frozen=True)
class ReadClaim:
    action_id: str
    user_id: int
    lease_token: str
    attempt_id: str
    action_version: int


def recovery(attempt):
    return dict((attempt.evidence or {}).get("reconciliation") or {})


def observe(attempt, code, now, **extra):
    state = recovery(attempt)
    observations = list(state.get("observations", []))
    value = {"round": state["rounds"], "code": code, "at": now.isoformat(), **extra}
    if observations and observations[-1]["round"] == state["rounds"]:
        observations[-1] = value
    else:
        observations.append(value)
    state["observations"] = observations[-MAX_ROUNDS:]
    attempt.evidence = {**(attempt.evidence or {}), "reconciliation": state}


async def claim_one(factory):
    if not get_settings().email_reconciliation_enabled:
        return None
    async with factory.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        rounds = func.coalesce(ActionAttempt.evidence["reconciliation"]["rounds"].as_integer(), 0)
        action = await session.scalar(
            select(AssistantAction)
            .join(AssistantTask, AssistantTask.id == AssistantAction.task_id)
            .join(ActionJob, ActionJob.action_id == AssistantAction.id)
            .join(ActionAttempt, ActionAttempt.action_id == AssistantAction.id)
            .where(
                AssistantAction.state == "outcome_unknown",
                AssistantAction.action_type == "send_email",
                ActionAttempt.state == "outcome_unknown",
                ActionJob.kind == "reconcile",
                or_(
                    and_(
                        ActionJob.state.in_(["held", "queued"]),
                        ActionJob.available_at <= now,
                        rounds < MAX_ROUNDS,
                    ),
                    and_(ActionJob.state == "running", ActionJob.lease_expires_at <= now),
                ),
            )
            .order_by(ActionJob.available_at, AssistantAction.id)
            .with_for_update(of=AssistantTask, skip_locked=True)
            .limit(1)
        )
        if action is None:
            return None
        task, action = await service.owned_action(session, action.user_id, action.id, lock=True)
        job = await session.get(ActionJob, action.id, with_for_update=True, populate_existing=True)
        attempt = await session.scalar(
            select(ActionAttempt)
            .where(ActionAttempt.action_id == action.id, ActionAttempt.state == "outcome_unknown")
            .with_for_update()
        )
        if action.state != "outcome_unknown" or attempt is None or job.kind != "reconcile":
            return None
        state = recovery(attempt)
        if job.state == "running":
            if job.lease_expires_at > now:
                return None
            observe(attempt, "read_lease_expired", now)
        elif job.state not in {"held", "queued"} or job.available_at > now:
            return None
        if state.get("rounds", 0) >= MAX_ROUNDS:
            worker.close_job(job, "held", "reconcile")
            worker.event(
                session, task, action, "action.reconciliation_required", code="budget_exhausted"
            )
            return None
        state = recovery(attempt)
        state["rounds"] = state.get("rounds", 0) + 1
        attempt.evidence = {**(attempt.evidence or {}), "reconciliation": state}
        observe(attempt, "reading", now)
        job.state, job.lease_token = "running", str(uuid4())
        job.lease_expires_at = now + timedelta(seconds=120)
        worker.event(session, task, action, "action.reconciliation_started", round=state["rounds"])
        return ReadClaim(action.id, action.user_id, job.lease_token, attempt.id, action.version)


def account_valid(user, action):
    return bool(
        user
        and user.google_connected
        and user.google_email_verified
        and user.google_sub == action.source_versions.get("google_subject")
        and (user.google_identity or {}).get("sub") == user.google_sub
        and user.email == action.payload["preview"]["from_address"]
        and GMAIL_READ_SCOPES.intersection(user.google_scopes or [])
    )


async def locked(session, claim):
    task, action = await service.owned_action(session, claim.user_id, claim.action_id, lock=True)
    user = await session.get(User, claim.user_id, with_for_update=True, populate_existing=True)
    job = await session.get(ActionJob, action.id, with_for_update=True, populate_existing=True)
    attempt = await session.get(
        ActionAttempt, claim.attempt_id, with_for_update=True, populate_existing=True
    )
    now = await session.scalar(select(func.clock_timestamp()))
    current = (
        action.state == "outcome_unknown"
        and action.version == claim.action_version
        and job.kind == "reconcile"
        and job.state == "running"
        and job.lease_token == claim.lease_token
        and job.lease_expires_at > now
        and attempt is not None
        and attempt.action_id == action.id
        and attempt.user_id == claim.user_id
        and attempt.state == "outcome_unknown"
    )
    return task, action, user, job, attempt, now, current


async def inputs(factory, claim):
    async with factory.begin() as session:
        _, action, user, _, attempt, _, current = await locked(session, claim)
        if not current:
            return None
        if not get_settings().email_reconciliation_enabled:
            raise ApiError(409, "recovery_disabled", "Recovery reads are disabled.")
        if not account_valid(user, action):
            raise ApiError(
                409,
                "recovery_access_unavailable",
                "Reconnect the original account with Gmail read access.",
            )
        try:
            gmail_sender.frozen_request(action)
        except ValueError:
            raise ApiError(
                409, "invalid_recovery_payload", "Saved payload validation failed."
            ) from None
        return (
            action.payload,
            attempt.dispatch_intent_at,
            user.google_account_version,
            (attempt.evidence or {}).get("late_response"),
        )


async def finish(factory, claim, outcome, account_version=None):
    async with factory.begin() as session:
        task, action, user, job, attempt, now, current = await locked(session, claim)
        if not current:
            return False
        if outcome.state == "succeeded":
            if not account_valid(user, action) or user.google_account_version != account_version:
                outcome = gmail_reconciliation.unknown("account_changed_during_read")
            else:
                # A response arriving during the GET must also be compatible.
                late = (attempt.evidence or {}).get("late_response")
                if late and (
                    late.get("state") == "failed"
                    or (
                        late.get("state") == "succeeded"
                        and (
                            late.get("message_id") != outcome.message_id
                            or late.get("thread_id") != outcome.thread_id
                        )
                    )
                ):
                    outcome = gmail_reconciliation.unknown("conflicting_late_response")
        observe(attempt, outcome.code, now)
        if outcome.state == "succeeded":
            attempt.state = "succeeded"
            attempt.evidence = {**attempt.evidence, "resolution": outcome.evidence()}
            await session.flush()
            action.state, action.version, action.error_code = "succeeded", action.version + 1, None
            action.result = {
                "gmail_message_id": outcome.message_id,
                "gmail_thread_id": outcome.thread_id,
            }
            worker.close_job(job, "done", "reconcile")
            worker.event(session, task, action, "action.state_changed", state="succeeded")
        else:
            rounds = recovery(attempt)["rounds"]
            worker.close_job(job, "held" if rounds >= MAX_ROUNDS else "queued", "reconcile")
            job.available_at = now + timedelta(seconds=DELAYS[rounds - 1])
            action.error_code = outcome.code
        worker.event(session, task, action, "action.reconciliation_observed", code=outcome.code)
        return True


async def run_once(factory, *, transport=None, token_loader=None):
    claim = await claim_one(factory)
    if claim is None:
        return False
    version = None
    try:
        # Validate before credential egress, then again after possible refresh/reconnect.
        if await inputs(factory, claim) is None:
            return True
        if token_loader:
            token = await token_loader(claim.user_id)
        else:
            async with factory() as session:
                token = await get_valid_access_token(session, User(id=claim.user_id))
        values = await inputs(factory, claim)
        if values is None:
            return True
        payload, dispatched_at, version, late = values
        outcome = await gmail_reconciliation.lookup(
            token, payload, dispatched_at, late_response=late, transport=transport
        )
    except ApiError as error:
        allowed = {
            "recovery_disabled",
            "recovery_access_unavailable",
            "invalid_recovery_payload",
            "reauth_required",
            "google_token_unavailable",
            "google_connection_changed",
        }
        outcome = gmail_reconciliation.unknown(
            error.code if error.code in allowed else "read_unavailable"
        )
    except Exception:
        outcome = gmail_reconciliation.unknown("read_interrupted")
    await finish(factory, claim, outcome, version)
    return True


def status(action, job, attempt):
    if action.state != "outcome_unknown":
        return None
    state = recovery(attempt) if attempt else {}
    observations = state.get("observations", [])
    rounds = state.get("rounds", 0)
    manual = not job or not attempt or rounds >= MAX_ROUNDS and job.state != "running"
    enabled = get_settings().email_reconciliation_enabled
    return {
        "status": "manual_inspection"
        if manual
        else "paused"
        if not enabled
        else "checking"
        if job.state == "running"
        else "scheduled",
        "rounds": rounds,
        "max_rounds": MAX_ROUNDS,
        "next_check_at": job.available_at.isoformat()
        if job and enabled and not manual and job.state != "running"
        else None,
        "last_code": observations[-1]["code"] if observations else None,
        "guidance": (
            "Check Sent in the original Google account. This email may already have been sent; "
            "do not resend based on an empty search."
        ),
    }
