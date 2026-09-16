"""Intent preview, saved contextual requests, and durable task history."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, Query
from pydantic import Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.assistant import continuation, draft_review, lookup_draft, mail_search, reads, steps, tasks
from app.assistant.context import capture_thread
from app.assistant.ui_context import capture_view
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
from app.schemas.compound import CompoundRequest
from app.schemas.continuation import TaskInputRequest
from app.schemas.draft_review import EditDraftRequest, ReviewDraftRequest
from app.schemas.lookup_draft import LookupDraftRequest
from app.schemas.mail_search import MailSearchRequest
from app.schemas.ui_context import UIContextSnapshotRequest
from app.workflows import registry

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/mail-search")
async def search_mail(request: MailSearchRequest, user_id: CurrentUser, session: DB):
    result = await mail_search.search(session, user_id, request)
    await session.commit()  # Release the sync fence and transaction-local timeouts.
    return result


@router.get("/workflows")
async def workflow_configuration(user_id: CurrentUser) -> dict:
    manifest = registry.load_manifest()
    return {
        "schema_version": "1.0",
        "operations": {
            op: {
                "implementation": manifest.operations[op].implementation if manifest else "native",
                "installed": True,
                "external_actions": False,
            }
            for op in registry.OPERATIONS
        },
        "mail_search": {
            "installed": True,
            "scope": "explicit_local_mailbox_window",
            "folders": ["all_synced", "INBOX", "SENT"],
            "max_window_days": 366,
            "page_size": mail_search.PAGE_SIZE,
            "external_actions": False,
        },
        "read_actions": {
            "release": reads.RELEASE,
            "operations": ["help", "search_mail", "transform_text"],
            "requires_explicit_read_options": True,
            "search_scope": "saved_capture",
            "page_size": reads.PAGE_SIZE,
            "external_actions": False,
        },
        "ui_context": {
            "capture_schema": "1.1",
            "surfaces": ["gmail_thread"],
            "max_visible_messages": 50,
            "reference_handlers": ["exact_message_excerpt", "single_message_summary"],
            "external_actions": False,
        },
        "continuation": {
            "installed": True,
            "schema_version": "1.0",
            "new_requests_only": True,
            "max_answer_rounds": continuation.MAX_INPUTS,
            "question_expiry_hours": continuation.QUESTION_HOURS,
        },
        "compound_templates": {
            "installed": True,
            "release": steps.RELEASE,
            "templates": [
                "summary_then_reply",
                "summary_then_compose",
                "lookup_then_reply",
                "lookup_then_compose",
            ],
            "lookup_release": lookup_draft.RELEASE,
            "lookup_scope": "saved_capture",
            "lookup_max_matches": reads.PAGE_SIZE,
            "entrypoint": "/assistant/compound-requests",
            "max_steps": 2,
            "requires_explicit_selection": True,
            "natural_language_planner": False,
            "external_actions": False,
        },
        "remote_resources_verified": False,
        "note": "Configuration only; source and remote prerequisites are checked per request.",
    }


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
    effective_input = await continuation.current_input(session, task)
    artifact_id = task.final_artifact_id if task.state == "succeeded" else None
    return {
        "task_id": task.id,
        "instruction": task.instruction,
        "read_options": task.read_input,
        "compound": await steps.view(session, task),
        "intent": (
            task.route["decision"]["intent"]
            if task.route
            else task.intent_hint
            if task.compound_input
            else "summarise"
            if task.release.get("workflow") == "summary-task-1.0.0"
            else None
        ),
        "route": task.route,
        "question": await continuation.question_view(session, task),
        "continuation_release": task.continuation_release,
        "input_version": task.input_version,
        "effective_context_snapshot_id": (
            effective_input.context_snapshot_id if effective_input else task.context_snapshot_id
        ),
        "effective_draft_input": effective_input.draft_input
        if effective_input
        else task.draft_input,
        "resolved_inputs": effective_input.effective_fields if effective_input else {},
        "draft_input": task.draft_input,
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
async def create_context(
    request: Annotated[
        ContextSnapshotRequest | UIContextSnapshotRequest, Field(discriminator="schema_version")
    ],
    user_id: CurrentUser,
    session: DB,
):
    snapshot = (
        await capture_view(session, user_id, request)
        if isinstance(request, UIContextSnapshotRequest)
        else await capture_thread(session, user_id, request.thread_id)
    )
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


@router.post("/compound-requests", status_code=202)
async def submit_compound(
    request: Annotated[CompoundRequest | LookupDraftRequest, Body(discriminator="template")],
    user_id: CurrentUser,
    session: DB,
):
    task = await tasks.submit(session, user_id, request.as_request(), compound=request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/tasks/{task_id}/inputs", status_code=202)
async def submit_input(task_id: str, request: TaskInputRequest, user_id: CurrentUser, session: DB):
    task = await continuation.accept_input(session, user_id, task_id, request)
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
    artifact = await draft_review.owned_artifact(session, user_id, artifact_id)
    task = await tasks.owned_task(session, user_id, artifact.task_id, lock=True)
    if task.release.get("workflow") == reads.RELEASE:
        snapshot = (
            await session.scalar(
                select(ContextSnapshot).where(
                    ContextSnapshot.id == task.context_snapshot_id,
                    ContextSnapshot.user_id == user_id,
                )
            )
            if task.context_snapshot_id
            else None
        )
        await reads.validate_source(
            session, user_id, snapshot.payload if snapshot else None, task.read_input["operation"]
        )
    return await draft_review.artifact_view(session, task, artifact)


@router.post("/tasks/{task_id}/draft-revisions", status_code=201)
async def edit_draft(task_id: str, request: EditDraftRequest, user_id: CurrentUser, session: DB):
    task, artifact = await draft_review.edit(session, user_id, task_id, request)
    result = await draft_review.artifact_view(session, task, artifact)
    await session.commit()
    return result


@router.get("/tasks/{task_id}/draft-revisions")
async def draft_history(
    task_id: str,
    user_id: CurrentUser,
    session: DB,
    before_revision: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    task = await tasks.owned_task(session, user_id, task_id, lock=True)
    current = await draft_review.latest(session, task)
    draft_review.require_draft(current)
    query = select(ArtifactRevision).where(
        ArtifactRevision.task_id == task.id,
        ArtifactRevision.user_id == user_id,
        ArtifactRevision.stream_key == current.stream_key,
    )
    if before_revision is not None:
        query = query.where(ArtifactRevision.revision < before_revision)
    rows = (
        await session.scalars(query.order_by(ArtifactRevision.revision.desc()).limit(page_size + 1))
    ).all()
    page = rows[:page_size]
    return {
        "revisions": [
            {"artifact_id": a.id, "revision": a.revision, "created_at": a.created_at.isoformat()}
            for a in page
        ],
        "latest_artifact_id": current.id,
        "latest_revision": current.revision,
        "next_before_revision": page[-1].revision if len(rows) > page_size else None,
    }


@router.post("/artifacts/{artifact_id}/review")
async def review_draft(
    artifact_id: str,
    request: ReviewDraftRequest,
    user_id: CurrentUser,
    session: DB,
):
    task, artifact = await draft_review.review(session, user_id, artifact_id, request)
    result = await draft_review.artifact_view(session, task, artifact)
    await session.commit()
    return result


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
