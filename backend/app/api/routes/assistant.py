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
from app.assistant import (
    command_plans,
    continuation,
    coordinator,
    draft_review,
    lookup_draft,
    mail_search,
    meeting_responses,
    planning,
    reads,
    scheduling,
    scheduling_proposals,
    source_data,
    steps,
    tasks,
    workflows,
)
from app.assistant.context import capture_thread
from app.assistant.source_data import context_data
from app.assistant.ui_context import capture_view
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import ArtifactRevision, AssistantTask, ContextSnapshot, TaskEvent
from app.mail.dependency import gmail_sources
from app.planner.intent_router import preview_route
from app.schemas.assistant import (
    AssistantRequest,
    CancelTaskRequest,
    ContextSnapshotRequest,
    RoutePreview,
    RoutePreviewRequest,
)
from app.schemas.command_plan import CommandPlanRequest, ConfirmCommandPlan
from app.schemas.compound import CompoundRequest
from app.schemas.continuation import TaskInputRequest
from app.schemas.coordinator import CoordinatorRequest
from app.schemas.draft_review import EditDraftRequest, ReviewDraftRequest
from app.schemas.lookup_draft import LookupDraftRequest
from app.schemas.mail_search import MailSearchRequest
from app.schemas.meeting_response import MeetingResponseRequest
from app.schemas.plan_review import ReviewPlan
from app.schemas.scheduling import SchedulingInputRequest, SchedulingRequest
from app.schemas.scheduling_proposal import ConfirmSchedulingProposal, SchedulingProposalRequest
from app.schemas.ui_context import UIContextSnapshotRequest
from app.schemas.workflow import WorkflowRequest
from app.workflows import auxiliary, registry

router = APIRouter(dependencies=[Depends(gmail_sources)])
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/mail-search")
async def search_mail(request: MailSearchRequest, user_id: CurrentUser, session: DB):
    if get_settings().gmail_source_mode == "on_demand":
        from app.mail.search import search

        return await search(user_id, request)
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
            "scope": "explicit_gmail_window"
            if get_settings().gmail_source_mode == "on_demand"
            else "explicit_local_mailbox_window",
            "source_mode": get_settings().gmail_source_mode,
            "stores_mail": get_settings().gmail_source_mode != "on_demand",
            "folders": ["all_mail", "INBOX", "SENT"]
            if get_settings().gmail_source_mode == "on_demand"
            else ["all_synced", "INBOX", "SENT"],
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
        "command_planner": {
            "installed": True,
            "release": command_plans.command.RELEASE,
            "entrypoint": "/assistant/command-plans",
            "requires_complete_command_review": True,
            "automatic_dispatch": False,
            "max_execution_steps": 2,
            "external_actions": False,
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
        "master_coordinator": {
            "installed": True,
            "entrypoint": "/assistant/workflow-proposals",
            "requires_complete_command_review": True,
            "automatic_dispatch": False,
            "max_steps": 3,
            "external_actions": False,
        },
        "mvp_workflows": {
            "entrypoint": "/assistant/workflow-requests",
            "singles": [
                "summary",
                "schedule",
                "plan",
                "draft_reply",
                "draft_new",
                "lookup_entity",
                "lookup_commitments",
            ],
            "compound": [
                ["schedule", "draft_reply"],
                ["schedule", "draft_new"],
                ["summary", "schedule", "draft_reply"],
                ["summary", "schedule", "draft_new"],
            ],
            "selected_plan_drafts": True,
            "meeting_response_entrypoint": "/assistant/meeting-response-proposals",
            "external_actions": False,
        },
        "auxiliary_generation": {
            op: {"implementation": entry.implementation, "external_actions": False}
            for op, entry in auxiliary.load_manifest().operations.items()
        },
        "calendar_booking": {
            "installed": True,
            "preview_entrypoint": "/assistant/artifacts/{id}/calendar-actions",
            "requires_separate_exact_approval": True,
            "scope": "single_event_in_owned_selected_calendar",
            "pilot_gated": True,
        },
        "remote_resources_verified": False,
        "scheduling": {
            "installed": True,
            "release": scheduling.RELEASE,
            "entrypoint": "/assistant/scheduling-requests",
            "operations": ["check_time", "suggest_slots"],
            "typed_constraints_required": True,
            "natural_language_extraction": True,
            "extraction": {
                "installed": True,
                "release": scheduling_proposals.extraction.RELEASE,
                "entrypoint": "/assistant/scheduling-proposals",
                "requires_complete_request_review": True,
                "live_model_evaluated": False,
            },
            "external_actions": False,
            "automatic_offer_creation": False,
        },
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
        **context_data(snapshot),
    }


async def task_view(session: AsyncSession, task: AssistantTask) -> dict:
    effective_input = await continuation.current_input(session, task)
    artifact_id = task.final_artifact_id if task.state == "succeeded" else None
    return {
        "task_id": task.id,
        "instruction": task.instruction,
        "read_options": task.read_input,
        "scheduling": task.scheduling_input,
        "compound": await steps.view(session, task),
        "workflow": await workflows.view(session, task),
        "intent": (
            task.route["decision"]["intent"]
            if task.route
            else task.intent_hint
            if task.compound_input or task.scheduling_input or task.workflow_input
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
    if get_settings().gmail_source_mode == "on_demand":
        snapshot = await source_data.capture(
            session, user_id, request.thread_id, getattr(request, "ui_map", None)
        )
    elif isinstance(request, UIContextSnapshotRequest):
        snapshot = await capture_view(session, user_id, request)
    else:
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


@router.post("/workflow-requests", status_code=202)
async def submit_workflow(request: WorkflowRequest, user_id: CurrentUser, session: DB):
    task = await tasks.submit(session, user_id, request.as_request(), workflow=request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/scheduling-proposals", status_code=202)
async def propose_scheduling(request: SchedulingProposalRequest, user_id: CurrentUser, session: DB):
    proposal, created = await scheduling_proposals.reserve(session, user_id, request)
    await session.commit()  # Never keep account/preferences/source locks across model inference.
    if created:
        state, result = await scheduling_proposals.interpret(proposal)
        proposal = await scheduling_proposals.complete(session, user_id, proposal.id, state, result)
    result = await scheduling_proposals.view(session, proposal)
    await session.commit()
    return result


@router.get("/scheduling-proposals/{proposal_id}")
async def get_scheduling_proposal(proposal_id: str, user_id: CurrentUser, session: DB):
    return await scheduling_proposals.view(
        session, await scheduling_proposals.owned(session, user_id, proposal_id)
    )


@router.post("/scheduling-proposals/{proposal_id}/confirm", status_code=202)
async def confirm_scheduling_proposal(
    proposal_id: str, request: ConfirmSchedulingProposal, user_id: CurrentUser, session: DB
):
    task = await scheduling_proposals.confirm(session, user_id, proposal_id, request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/scheduling-requests", status_code=202)
async def submit_scheduling(request: SchedulingRequest, user_id: CurrentUser, session: DB):
    task = await tasks.submit(session, user_id, request.as_request(), schedule=request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/tasks/{task_id}/scheduling-inputs", status_code=202)
async def submit_scheduling_input(
    task_id: str,
    request: SchedulingInputRequest,
    user_id: CurrentUser,
    session: DB,
):
    task = await scheduling.accept_input(session, user_id, task_id, request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/command-plans", status_code=202)
async def propose_command_plan(request: CommandPlanRequest, user_id: CurrentUser, session: DB):
    plan, created = await command_plans.reserve(session, user_id, request)
    await session.commit()  # No database transaction spans planner inference.
    if created:
        state, result = await command_plans.interpret(plan)
        plan = await command_plans.complete(session, user_id, plan.id, state, result)
    result = await command_plans.view(session, plan)
    await session.commit()
    return result


@router.get("/command-plans/{plan_id}")
async def get_command_plan(plan_id: str, user_id: CurrentUser, session: DB):
    return await command_plans.view(session, await command_plans.owned(session, user_id, plan_id))


@router.post("/command-plans/{plan_id}/confirm", status_code=202)
async def confirm_command_plan(
    plan_id: str, request: ConfirmCommandPlan, user_id: CurrentUser, session: DB
):
    task = await command_plans.confirm(session, user_id, plan_id, request)
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
    task = await tasks.owned_task(session, user_id, artifact.task_id)
    if task.scheduling_input is not None:
        # Calendar paths take account/preferences/source before task locks.
        result = await draft_review.artifact_view(session, task, artifact)
        result["scheduling_status"] = await scheduling.artifact_usability(session, task, artifact)
        return result
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
            session,
            user_id,
            context_data(snapshot) if snapshot else None,
            task.read_input["operation"],
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


@router.post("/tasks/{task_id}/plan-review")
async def review_plan(task_id: str, request: ReviewPlan, user_id: CurrentUser, session: DB):
    task, artifact = await planning.review(session, user_id, task_id, request)
    result = await draft_review.artifact_view(session, task, artifact)
    await session.commit()
    return result


@router.post("/workflow-proposals", status_code=202)
async def propose_workflow(request: CoordinatorRequest, user_id: CurrentUser, session: DB):
    row, created = await coordinator.reserve(session, user_id, request)
    await session.commit()
    if created:
        state, result = await coordinator.interpret(row)
        row = await command_plans.complete(session, user_id, row.id, state, result)
    result = await command_plans.view(session, row)
    await session.commit()
    return result


@router.get("/workflow-proposals/{proposal_id}")
async def get_workflow_proposal(proposal_id: str, user_id: CurrentUser, session: DB):
    return await command_plans.view(session, await coordinator.owned(session, user_id, proposal_id))


@router.post("/workflow-proposals/{proposal_id}/confirm", status_code=202)
async def confirm_workflow_proposal(
    proposal_id: str, request: ConfirmCommandPlan, user_id: CurrentUser, session: DB
):
    task = await coordinator.confirm(session, user_id, proposal_id, request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.post("/meeting-response-proposals", status_code=202)
async def propose_meeting_response(
    request: MeetingResponseRequest, user_id: CurrentUser, session: DB
):
    row, created = await meeting_responses.reserve(session, user_id, request)
    await session.commit()
    if created:
        state, result = await meeting_responses.interpret(row)
        row = await command_plans.complete(session, user_id, row.id, state, result)
    result = await command_plans.view(session, row)
    await session.commit()
    return result


@router.get("/meeting-response-proposals/{proposal_id}")
async def get_meeting_response(proposal_id: str, user_id: CurrentUser, session: DB):
    return await command_plans.view(
        session, await meeting_responses.owned(session, user_id, proposal_id)
    )


@router.post("/meeting-response-proposals/{proposal_id}/confirm", status_code=202)
async def confirm_meeting_response(
    proposal_id: str, request: ConfirmCommandPlan, user_id: CurrentUser, session: DB
):
    task = await meeting_responses.confirm(session, user_id, proposal_id, request)
    result = await task_view(session, task)
    await session.commit()
    return result


@router.get("/operational-status")
async def operational_status(user_id: CurrentUser, session: DB):
    from app.operations.status import snapshot

    return await snapshot(session, user_id)
