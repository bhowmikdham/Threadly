"""Intent preview, saved contextual requests, and durable task history."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.assistant import tasks
from app.assistant.context import capture_thread
from app.db.engine import get_session
from app.db.models import ArtifactRevision, AssistantTask, ContextSnapshot, TaskEvent
from app.planner.intent_router import preview_route
from app.schemas.assistant import (
    AssistantRequest,
    CancelTaskRequest,
    ContextSnapshotRequest,
    RoutePreview,
    RoutePreviewRequest,
)

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/route-preview", response_model=RoutePreview)
async def route_preview(request: RoutePreviewRequest, user_id: CurrentUser) -> RoutePreview:
    # JWT authorizes inference only; no user ID or context is accepted from the model.
    return await preview_route(request)


def context_view(snapshot: ContextSnapshot) -> dict:
    return {
        "context_snapshot_id": snapshot.id,
        "captured_at": snapshot.created_at.isoformat(),
        "source_hash": snapshot.source_hash,
        **snapshot.payload,
    }


async def task_view(session: AsyncSession, task: AssistantTask) -> dict:
    artifact_id = None
    if task.state == "succeeded":
        artifact_id = await session.scalar(
            select(ArtifactRevision.id).where(
                ArtifactRevision.task_id == task.id, ArtifactRevision.user_id == task.user_id
            )
        )
    return {
        "task_id": task.id,
        "instruction": task.instruction,
        "intent": (
            task.route["decision"]["intent"]
            if task.route
            else "summarise"
            if task.release.get("workflow") == "summary-task-1.0.0"
            else None
        ),
        "route": task.route,
        "state": task.state,
        "version": task.version,
        "latest_sequence": task.latest_sequence,
        "context_snapshot_id": task.context_snapshot_id,
        "artifact_id": artifact_id,
        "error_code": task.error_code,
        "release": task.release,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "events_url": f"/assistant/tasks/{task.id}/events",
    }


@router.post("/context-snapshots", status_code=201)
async def create_context(request: ContextSnapshotRequest, user_id: CurrentUser, session: DB):
    snapshot = await capture_thread(session, user_id, request.thread_id)
    result = context_view(snapshot)
    await session.commit()
    return result


@router.get("/context-snapshots/{context_id}")
async def get_context(context_id: str, user_id: CurrentUser, session: DB):
    snapshot = (
        await session.execute(
            select(ContextSnapshot).where(
                ContextSnapshot.id == context_id, ContextSnapshot.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise ApiError(404, "not_found", "Unknown context snapshot.")
    return context_view(snapshot)


@router.post("/requests", status_code=202)
async def submit_request(request: AssistantRequest, user_id: CurrentUser, session: DB):
    task = await tasks.submit(session, user_id, request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user_id: CurrentUser, session: DB):
    return await task_view(session, await tasks.owned_task(session, user_id, task_id))


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: CancelTaskRequest, user_id: CurrentUser, session: DB):
    task = await tasks.cancel(session, user_id, task_id, request.expected_version)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.get("/tasks")
async def list_tasks(
    user_id: CurrentUser,
    session: DB,
    cursor: Annotated[str | None, Query(max_length=36)] = None,
    state: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "needs_clarification",
        "unsupported",
    ]
    | None = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    q = select(AssistantTask).where(AssistantTask.user_id == user_id)
    if cursor:
        previous = await tasks.owned_task(session, user_id, cursor)
        q = q.where(
            or_(
                AssistantTask.created_at < previous.created_at,
                and_(
                    AssistantTask.created_at == previous.created_at, AssistantTask.id < previous.id
                ),
            )
        )
    if state:
        q = q.where(AssistantTask.state == state)
    rows = (
        (
            await session.execute(
                q.order_by(AssistantTask.created_at.desc(), AssistantTask.id.desc()).limit(
                    page_size + 1
                )
            )
        )
        .scalars()
        .all()
    )
    page = rows[:page_size]
    return {
        "tasks": [await task_view(session, task) for task in page],
        "next_cursor": page[-1].id if len(rows) > page_size else None,
    }


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, user_id: CurrentUser, session: DB):
    artifact = (
        await session.execute(
            select(ArtifactRevision).where(
                ArtifactRevision.id == artifact_id, ArtifactRevision.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise ApiError(404, "not_found", "Unknown artifact.")
    return {
        "artifact_id": artifact.id,
        "task_id": artifact.task_id,
        "revision": artifact.revision,
        "artifact": artifact.payload,
        "provenance": artifact.provenance,
    }


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    user_id: CurrentUser,
    session: DB,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
):
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
            if cursor < 0 or (after and after != cursor):
                raise ValueError
            after = cursor
        except ValueError:
            raise ApiError(
                422, "invalid_event_cursor", "Invalid or conflicting event cursor."
            ) from None
    task = await tasks.owned_task(session, user_id, task_id)
    if after > task.latest_sequence:
        raise ApiError(409, "event_cursor_ahead", "Event cursor is ahead of this task.")
    rows = (
        (
            await session.execute(
                select(TaskEvent)
                .where(
                    TaskEvent.task_id == task_id,
                    TaskEvent.user_id == user_id,
                    TaskEvent.sequence > after,
                )
                .order_by(TaskEvent.sequence)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    events = [
        {
            "id": str(e.sequence),
            "event": e.kind,
            "data": json.dumps(
                {
                    "sequence": e.sequence,
                    "task_version": e.task_version,
                    "created_at": e.created_at.isoformat(),
                    "payload": e.payload,
                }
            ),
        }
        for e in rows
    ]
    # Finite replay batch: release DB connection before streaming to the client.
    await session.rollback()

    async def replay():
        for event in events:
            yield event

    return EventSourceResponse(replay())
