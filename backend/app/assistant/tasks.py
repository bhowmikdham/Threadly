"""Atomic request acceptance, task transitions, event history and fenced job claims.

Every mutation locks the task first. Network inference belongs outside these
transactions. Expired leases may repeat generation, never publish two artifacts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.assistant import continuation, reads, summary_quality
from app.assistant.drafting import bind_input
from app.assistant.summary import digest
from app.assistant.ui_routing import wrap_release
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantStep,
    AssistantTask,
    ContextSnapshot,
    TaskEvent,
    User,
)
from app.schemas.assistant import AssistantRequest
from app.workflows.registry import release_manifest

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "needs_clarification", "unsupported"}


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


async def submit(
    session: AsyncSession,
    user_id: int,
    request: AssistantRequest,
    *,
    compound=None,
    schedule=None,
    schedule_binding=None,
    workflow=None,
) -> AssistantTask:
    value = request.model_dump()
    if request.draft_options is None:
        value.pop(
            "draft_options"
        )  # Preserve replay hashes for requests accepted before this field.
    if request.read_options is None:
        value.pop("read_options")  # Retain historical request hashes.
    if compound is not None:
        value["compound_input"] = compound.model_dump()
    if schedule is not None:
        value["scheduling_input"] = schedule.model_dump()
    if schedule_binding is not None:
        if schedule is None or schedule_binding["request"] != schedule.model_dump():
            raise ValueError("Scheduling binding does not match the request")
        value["schedule_binding"] = schedule_binding
    if workflow is not None:
        value["workflow_input"] = workflow.model_dump()
    request_hash = digest(value)
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
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(401, "unauthorized", "The account no longer exists.")
    if request.continuation is not None:
        raise ApiError(
            501, "continuation_not_available", "Saved task continuation is not available yet."
        )
    context = None
    if request.context_snapshot_id is not None:
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
    if request.read_options is not None:
        reads.validate_input(
            request.read_options, context, request.draft_options, request.intent_hint
        )
    draft_input = await bind_input(session, user, request.draft_options, context)
    scheduling_input = None
    if schedule is not None:
        from app.assistant import scheduling

        if schedule_binding is not None:
            await scheduling.check_current(
                session, user_id, schedule_binding, request.context_snapshot_id
            )
            scheduling_input = schedule_binding
        else:
            scheduling_input = await scheduling.bind_input(session, user_id, schedule, context)
        accepted_release = scheduling.release_manifest()
    else:
        accepted_release = summary_quality.wrap_release(release_manifest())
    if schedule is None and context and context.payload.get("schema_version") == "1.1":
        accepted_release = wrap_release(accepted_release)
    if request.read_options is not None:
        accepted_release = reads.wrap_release(accepted_release)
    if compound is not None:
        from app.assistant import lookup_draft, steps
        from app.schemas.lookup_draft import LookupDraftRequest

        steps.validate_input(compound, context, draft_input)
        accepted_release = (
            lookup_draft if isinstance(compound, LookupDraftRequest) else steps
        ).wrap_release(accepted_release)
    workflow_input = None
    if workflow is not None:
        from app.assistant import workflows

        workflow_input = await workflows.bind_input(
            session, user_id, workflow, context, draft_input
        )
        accepted_release = workflows.release_manifest()
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
                compound_input=compound.model_dump() if compound else None,
                scheduling_input=scheduling_input,
                workflow_input=workflow_input,
                read_input=request.read_options.model_dump() if request.read_options else None,
                continuation_release=(
                    scheduling.continuation_manifest()
                    if schedule
                    else None
                    if compound is not None or workflow is not None
                    else continuation.release_manifest()
                ),
                context_snapshot_id=context.id if context else None,
                intent_hint=request.intent_hint,
                draft_input=draft_input,
                state="queued",
                version=1,
                latest_sequence=1,
                release=accepted_release,
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
    resumable = task.state == "needs_clarification" and task.continuation_release is not None
    if task.state in TERMINAL_STATES and not resumable:
        raise ApiError(409, "task_finished", "The task has already finished.")
    job = await session.get(AssistantJob, task.id)
    await continuation.cancel_question(session, task)
    task.state, task.error_code = "cancelled", None
    task.version += 1
    close_job(job)
    await session.execute(
        update(AssistantStep)
        .where(
            AssistantStep.task_id == task.id,
            AssistantStep.user_id == user_id,
            AssistantStep.state.in_(["pending", "running", "failed"]),
        )
        .values(state="cancelled", error_code="task_cancelled")
    )
    add_event(session, task, "task.finished", {"state": "cancelled"})
    await session.flush()
    await session.refresh(task)
    return task


@dataclass(frozen=True)
class JobClaim:
    task_id: str
    user_id: int
    token: str
    context_id: str | None
    snapshot: dict | None
    release: dict
    instruction: str
    intent_hint: str | None
    route: dict | None
    draft_input: dict | None
    continuation_release: dict | None = None
    input_version: int = 0
    resolved_inputs: dict | None = None
    request_created_at: datetime | None = None
    read_input: dict | None = None
    compound_input: dict | None = None
    scheduling_input: dict | None = None
    workflow_input: dict | None = None


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
        await session.execute(
            update(AssistantStep)
            .where(
                AssistantStep.task_id == task.id,
                AssistantStep.user_id == task.user_id,
                AssistantStep.state.in_(["pending", "running"]),
            )
            .values(state="failed", error_code="attempts_exhausted")
        )
        add_event(
            session, task, "task.finished", {"state": "failed", "error_code": task.error_code}
        )
        return None
    try:
        effective = await continuation.current_input(session, task)
    except ApiError as exc:
        task.state, task.error_code = "failed", exc.code
        task.version += 1
        close_job(job)
        add_event(session, task, "task.finished", {"state": "failed", "error_code": exc.code})
        return None
    context_id = effective.context_snapshot_id if effective else task.context_snapshot_id
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
                ContextSnapshot.id == context_id,
                ContextSnapshot.user_id == task.user_id,
            )
        )
    ).scalar_one_or_none()
    await session.flush()
    return JobClaim(
        task.id,
        task.user_id,
        token,
        context.id if context else None,
        context.payload if context else None,
        task.release,
        task.instruction,
        task.intent_hint,
        task.route,
        effective.draft_input if effective else task.draft_input,
        task.continuation_release,
        task.input_version,
        effective.effective_fields if effective else {},
        task.created_at,
        task.read_input,
        task.compound_input,
        task.scheduling_input,
        task.workflow_input,
    )


async def finish(
    session: AsyncSession,
    claim: JobClaim,
    *,
    payload: dict | None = None,
    provenance: dict | None = None,
    error_code: str | None = None,
    retryable: bool = False,
    stopped_state: str | None = None,
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
    if stopped_state:
        if stopped_state not in {"needs_clarification", "unsupported"}:
            raise ValueError("invalid stopped state")
        if stopped_state == "needs_clarification":
            question_error = continuation.open_question(
                session,
                task,
                now,
                context_id=claim.context_id,
                source_hash=digest(claim.snapshot) if claim.snapshot else None,
            )
            if question_error:
                stopped_state, error_code = "unsupported", question_error
        task.state, task.error_code = stopped_state, error_code
        close_job(job)
        add_event(
            session, task, "task.finished", {"state": stopped_state, "error_code": error_code}
        )
    elif error_code:
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
                draft_envelope=claim.draft_input if payload.get("kind") == "draft" else None,
            )
        )
        task.final_artifact_id = artifact_id
        task.state, task.error_code = "succeeded", None
        close_job(job)
        add_event(session, task, "artifact.ready", {"artifact_id": artifact_id, "revision": 1})
        add_event(
            session, task, "task.finished", {"state": "succeeded", "artifact_id": artifact_id}
        )
    await session.flush()
    return True


async def save_route(session: AsyncSession, claim: JobClaim, route: dict) -> bool:
    """Checkpoint the validated proposal only while this worker still owns its lease."""
    task = (
        await session.execute(
            select(AssistantTask)
            .where(
                AssistantTask.id == claim.task_id,
                AssistantTask.user_id == claim.user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
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
    if task.route is not None:
        return task.route == route
    task.route = route
    task.version += 1
    add_event(
        session,
        task,
        "task.routed",
        {"intent": route["decision"]["intent"], "route_status": route["decision"]["status"]},
    )
    await session.flush()
    return True
