"""Prefetch owned source references before API handlers acquire database locks."""

from fastapi import Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.assistant import source_data
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import (
    ArtifactRevision,
    AssistantAction,
    AssistantTask,
    CommandPlan,
    ContextSnapshot,
    MeetingNegotiation,
    MeetingSelection,
    SchedulingProposal,
    Thread,
)


async def references(owner, identifiers, *, factory=None):
    """Resolve only exact caller-supplied IDs, with ownership on every join/read."""
    ids = {i for i in identifiers if isinstance(i, str) and 0 < len(i) <= 128}
    if not ids:
        return []
    async with (factory or get_session_factory())() as session:
        for model in (AssistantAction, ArtifactRevision):
            rows = (
                await session.scalars(
                    select(model).where(model.user_id == owner, model.id.in_(ids))
                )
            ).all()
            ids.update(row.task_id for row in rows)
            if model is AssistantAction:
                ids.update(
                    row.source_versions.get("negotiation_id")
                    for row in rows
                    if row.source_versions.get("negotiation_id")
                )
            if model is ArtifactRevision:
                ids.update(
                    row.payload.get("context_snapshot_id")
                    for row in rows
                    if row.payload.get("context_snapshot_id")
                )
        for model in (CommandPlan, SchedulingProposal, AssistantTask):
            rows = (
                await session.scalars(
                    select(model).where(model.user_id == owner, model.id.in_(ids))
                )
            ).all()
            if model is AssistantTask:
                ids.update(
                    row.effective_context_snapshot_id or row.context_snapshot_id
                    for row in rows
                    if row.effective_context_snapshot_id or row.context_snapshot_id
                )
            else:
                ids.update(row.context_snapshot_id for row in rows if row.context_snapshot_id)
        selections = (
            await session.scalars(
                select(MeetingSelection).where(
                    MeetingSelection.user_id == owner, MeetingSelection.id.in_(ids)
                )
            )
        ).all()
        ids.update(row.negotiation_id for row in selections)
        negotiations = (
            await session.scalars(
                select(MeetingNegotiation).where(
                    MeetingNegotiation.user_id == owner, MeetingNegotiation.id.in_(ids)
                )
            )
        ).all()
        thread_ids = {row.thread_id for row in negotiations}
        thread_ids.update(
            (
                await session.scalars(
                    select(Thread.id).where(
                        Thread.user_id == owner, Thread.gmail_thread_id.in_(ids)
                    )
                )
            ).all()
        )
        for thread_id in thread_ids:
            latest = await session.scalar(
                select(ContextSnapshot)
                .where(ContextSnapshot.user_id == owner, ContextSnapshot.thread_id == thread_id)
                .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.id.desc())
                .limit(1)
            )
            if latest:
                ids.add(latest.id)
        rows = (
            await session.scalars(
                select(ContextSnapshot).where(
                    ContextSnapshot.user_id == owner, ContextSnapshot.id.in_(ids)
                )
            )
        ).all()
        return [row.payload for row in rows]


async def gmail_sources(request: Request, response: Response, owner: CurrentUser):
    if get_settings().gmail_source_mode != "on_demand":
        yield
        return
    response.headers["Cache-Control"] = "no-store"
    async with source_data.source_scope():
        body = {}
        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                body = await request.json()
            except ValueError:
                pass
        if not isinstance(body, dict):
            body = {}
        # Do not hydrate when polling task progress, cancelling, or rejecting.
        passive = request.url.path.endswith(("/cancel", "/reject", "/events", "/close")) or (
            request.method == "GET" and "/tasks" in request.url.path
        )
        if not passive:
            identifiers = list(request.path_params.values())
            if request.url.path.endswith(("/inputs", "/scheduling-inputs")) and body.get(
                "context_snapshot_id"
            ):
                # A replacement capture is allowed precisely because the old source changed.
                identifiers = []
            identifiers.append(request.query_params.get("context_snapshot_id"))
            identifiers += [
                body.get(k)
                for k in ("context_snapshot_id", "plan_artifact_id", "artifact_id", "selection_id")
            ]
            if "/calendar/negotiations" in request.url.path:
                identifiers.append(body.get("thread_id"))
            await source_data.prefetch(owner, await references(owner, identifiers))
        if request.url.path.endswith("/context-snapshots") and request.method == "POST":
            thread_id = body.get("thread_id")
            if isinstance(thread_id, str):
                await source_data.fetch(owner, thread_id)
        yield
