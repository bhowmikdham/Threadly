"""Atomic request acceptance, task transitions, event history and fenced job claims.

Every mutation locks the task first. Network inference belongs outside these
transactions. Expired leases may repeat generation, never publish two artifacts.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.assistant.summary import digest, release_manifest
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantTask,
    ContextSnapshot,
    TaskEvent,
    User,
)
from app.schemas.assistant import AssistantRequest

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
SUMMARY_COMMANDS = {
    "summarise this",
    "summarize this",
    "summarise this thread",
    "summarize this thread",
    "summarise this email",
    "summarize this email",
}
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


async def owned_task(
    session: AsyncSession, user_id: int, task_id: str, *, lock=False
) -> AssistantTask:
    q = select(AssistantTask).where(AssistantTask.id == task_id, AssistantTask.user_id == user_id)
    if lock:
        q = q.with_for_update()
    task = (await session.execute(q)).scalar_one_or_none()
    if task is None:
        raise ApiError(404, "not_found", "Unknown assistant task.")
    return task


async def submit(session: AsyncSession, user_id: int, request: AssistantRequest) -> AssistantTask:
    request_hash = digest(request.model_dump())
    existing = (
        await session.execute(
            select(AssistantTask).where(
                AssistantTask.user_id == user_id, AssistantTask.request_id == request.request_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.request_hash != request_hash:
            raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
        return existing
    if await session.get(User, user_id) is None:
        raise ApiError(401, "unauthorized", "The account no longer exists.")
    normalized = request.instruction.strip().casefold().rstrip(".!?")
    if request.continuation is not None:
        raise ApiError(
            501, "continuation_not_available", "Saved task continuation is not available yet."
        )
    if request.intent_hint not in (None, "summarise") or normalized not in SUMMARY_COMMANDS:
        raise ApiError(
            501, "workflow_not_available", "This release executes explicit thread summaries only."
        )
    context = (
        await session.execute(
            select(ContextSnapshot).where(
                ContextSnapshot.id == request.context_snapshot_id,
                ContextSnapshot.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if context is None:
        raise ApiError(404, "context_not_found", "Select an accessible saved thread snapshot.")
    task_id = str(uuid4())
    inserted = (
        await session.execute(
            insert(AssistantTask)
            .values(
                id=task_id,
                user_id=user_id,
                request_id=request.request_id,
                request_hash=request_hash,
                instruction=request.instruction,
                context_snapshot_id=context.id,
                state="queued",
                version=1,
                latest_sequence=1,
                release=release_manifest(),
            )
            .on_conflict_do_nothing(constraint="uq_task_request")
            .returning(AssistantTask.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        # The conflicting insert is visible after its transaction commits.
        existing = (
            await session.execute(
                select(AssistantTask).where(
                    AssistantTask.user_id == user_id, AssistantTask.request_id == request.request_id
                )
            )
        ).scalar_one()
        if existing.request_hash != request_hash:
            raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
        return existing
    session.add(AssistantJob(task_id=task_id, user_id=user_id, state="queued", attempts=0))
    session.add(
        TaskEvent(
            task_id=task_id,
            user_id=user_id,
            sequence=1,
            task_version=1,
            kind="task.accepted",
            payload={"state": "queued"},
        )
    )
    await session.flush()
    return await owned_task(session, user_id, task_id)


def add_event(session: AsyncSession, task: AssistantTask, kind: str, payload: dict) -> None:
    task.latest_sequence += 1
    session.add(
        TaskEvent(
            task_id=task.id,
            user_id=task.user_id,
            sequence=task.latest_sequence,
            task_version=task.version,
            kind=kind,
            payload=payload,
        )
    )


def close_job(job: AssistantJob) -> None:
    job.state, job.lease_token, job.lease_expires_at = "done", None, None


async def cancel(session: AsyncSession, user_id: int, task_id: str, version: int) -> AssistantTask:
    task = await owned_task(session, user_id, task_id, lock=True)
    if task.state == "cancelled" and version in (task.version, task.version - 1):
        return task
    if task.version != version:
        raise ApiError(409, "version_conflict", "Task changed; reload its current state.")
    if task.state in TERMINAL_STATES:
        raise ApiError(409, "task_finished", "The task has already finished.")
    job = await session.get(AssistantJob, task.id)
    task.state, task.error_code = "cancelled", None
    task.version += 1
    close_job(job)
    add_event(session, task, "task.finished", {"state": "cancelled"})
    await session.flush()
    await session.refresh(task)
    return task


@dataclass(frozen=True)
class JobClaim:
    task_id: str
    user_id: int
    token: str
    context_id: str
    snapshot: dict
    release: dict


async def claim_next(session: AsyncSession) -> JobClaim | None:
    now = await session.scalar(select(func.clock_timestamp()))
    task = (
        await session.execute(
            select(AssistantTask)
            .join(AssistantJob, AssistantJob.task_id == AssistantTask.id)
            .where(
                AssistantTask.state.in_(["queued", "running"]),
                or_(
                    and_(AssistantJob.state == "queued", AssistantJob.available_at <= now),
                    and_(AssistantJob.state == "running", AssistantJob.lease_expires_at <= now),
                ),
            )
            .order_by(AssistantJob.available_at, AssistantTask.id)
            .with_for_update(of=AssistantTask, skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if task is None:
        return None
    job = await session.get(AssistantJob, task.id)
    if job.attempts >= MAX_ATTEMPTS:
        task.state, task.error_code = "failed", "attempts_exhausted"
        task.version += 1
        close_job(job)
        add_event(
            session, task, "task.finished", {"state": "failed", "error_code": task.error_code}
        )
        return None
    token = str(uuid4())
    job.state, job.lease_token = "running", token
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    job.attempts += 1
    task.state, task.error_code = "running", None
    task.version += 1
    add_event(session, task, "task.stage_changed", {"state": "running", "attempt": job.attempts})
    context = (
        await session.execute(
            select(ContextSnapshot).where(
                ContextSnapshot.id == task.context_snapshot_id,
                ContextSnapshot.user_id == task.user_id,
            )
        )
    ).scalar_one()
    await session.flush()
    return JobClaim(task.id, task.user_id, token, context.id, context.payload, task.release)


async def finish(
    session: AsyncSession,
    claim: JobClaim,
    *,
    payload: dict | None = None,
    provenance: dict | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> bool:
    task = (
        await session.execute(
            select(AssistantTask)
            .where(AssistantTask.id == claim.task_id, AssistantTask.user_id == claim.user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:  # account/thread deleted while inference was in flight
        return False
    job = await session.get(AssistantJob, task.id)
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        task.state != "running"
        or job.state != "running"
        or job.lease_token != claim.token
        or job.lease_expires_at <= now
    ):
        return False
    task.version += 1
    if error_code:
        task.error_code = error_code
        if retryable and job.attempts < MAX_ATTEMPTS:
            task.state, job.state = "queued", "queued"
            job.lease_token, job.lease_expires_at = None, None
            job.available_at = now + timedelta(seconds=2**job.attempts)
            add_event(session, task, "task.stage_changed", {"state": "queued", "retrying": True})
        else:
            task.state = "failed"
            close_job(job)
            add_event(session, task, "task.finished", {"state": "failed", "error_code": error_code})
    else:
        if payload is None or provenance is None:
            raise ValueError("successful completion requires an artifact and provenance")
        artifact_id = str(uuid4())
        session.add(
            ArtifactRevision(
                id=artifact_id,
                task_id=task.id,
                user_id=task.user_id,
                revision=1,
                payload=payload,
                provenance=provenance,
            )
        )
        task.state, task.error_code = "succeeded", None
        close_job(job)
        add_event(session, task, "artifact.ready", {"artifact_id": artifact_id, "revision": 1})
        add_event(
            session, task, "task.finished", {"state": "succeeded", "artifact_id": artifact_id}
        )
    await session.flush()
    return True
