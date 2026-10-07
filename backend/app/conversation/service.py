"""Conversation API service. Never hold a DB transaction across provider/model calls."""

import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import email_draft, engine, store
from app.conversation.runtime import Runtime, proposal_text
from app.db.engine import get_session_factory
from app.db.models import Conversation, User


async def turn(owner, request, *, factory=None, model=None):
    if not get_settings().conversation_enabled:
        raise ApiError(
            503,
            "conversation_disabled",
            "Conversation updates are being installed. Try again shortly.",
        )
    factory = factory or get_session_factory()
    async with factory.begin() as session:
        row, state, lease, replay = await store.claim(session, owner, request)
    if replay:
        return await hydrate_response(owner, replay, factory)
    runtime = Runtime(owner, request, state, factory, lease)
    started = time.monotonic()
    try:
        if state.get("pending_result"):
            response = await hydrate_response(owner, state["pending_result"], factory)
        elif getattr(request, "calendar_choice_id", None):
            from app.calendar.event_choices import select

            response = await select(runtime, request.calendar_choice_id)
            response["release"] = engine.RELEASE
        else:
            context = await runtime.context()
            response = await engine.run(context, runtime, model)
        from app.calendar import event_choices

        if choices := event_choices.public(state):
            response.setdefault("calendar_choices", choices)
        if runtime.search_page is not None:
            response["search"] = runtime.search_page
        response.setdefault("context_references", runtime.turn_source_references)
        response["active_email_draft_id"] = (response.get("email_draft") or {}).get(
            "draft_id"
        ) or email_draft.active_text_id(state)
        response["latency_ms"] = round((time.monotonic() - started) * 1000)
        if getattr(request, "calendar_choice_id", None) and not state.get("pending_result"):
            async with factory.begin() as session:
                await store.checkpoint(session, owner, request, lease, state, response)
        async with factory.begin() as session:
            return await store.complete(session, owner, request, lease, state, response)
    except BaseException:
        # Also releases on cancelled HTTP callers; a retry uses the same task/proposal key.
        async with factory.begin() as session:
            await store.release_failed(session, owner, request.conversation_id, lease)
        raise


async def hydrate_response(owner, saved, factory):
    from app.api.routes.assistant import task_view
    from app.assistant import command_plans, coordinator, source_data, tasks
    from app.db.models import ContextSnapshot

    result = dict(saved)
    async with factory() as session:
        if saved.get("calendar_action_id"):
            from app.calendar.event_creation import response

            result.update(await response(session, owner, saved["calendar_action_id"]))
        if saved.get("task_id"):
            result["task"] = await task_view(
                session, await tasks.owned_task(session, owner, saved["task_id"])
            )
        if saved.get("proposal_id"):
            row = await coordinator.owned(session, owner, saved["proposal_id"])
            if row.state == "planning":
                source = (
                    await session.get(ContextSnapshot, row.context_snapshot_id)
                    if row.context_snapshot_id
                    else None
                )
                reference = source.payload if source else None
                await session.commit()
                if reference:
                    await source_data.prefetch(owner, [reference])
                status, value = await coordinator.interpret(row)
                row = await command_plans.complete(session, owner, row.id, status, value)
            result["proposal"] = await command_plans.view(session, row)
            result["text"] = proposal_text(result["proposal"]["state"])
            await session.commit()
    # No original search snippets are persisted or replayed from stale storage.
    if saved.get("trace") and any(
        t["tool"] in {"search_mail", "more_mail"} for t in saved["trace"]
    ):
        result["notice"] = "Search cards aren’t saved. Ask to search again for current emails."
    return result


async def choose_calendar(owner, identifier, selection, *, factory=None):
    from app.schemas.conversation import CalendarChoiceTurn

    request = CalendarChoiceTurn(
        conversation_id=identifier,
        request_id=selection.request_id,
        expected_version=selection.expected_version,
        instruction="Use the selected calendar for this event.",
        calendar_choice_id=selection.choice_id,
    )
    return await turn(owner, request, factory=factory)


async def get(owner, identifier, factory=None):
    from app.calendar import event_choices

    factory = factory or get_session_factory()
    async with factory() as session:
        row = await store.owned(session, owner, identifier)
        state = store.decode(row)
        proposal = None
        if state.get("proposal_id"):
            from app.assistant import command_plans, coordinator

            active = await coordinator.owned(session, owner, state["proposal_id"])
            proposal = await command_plans.view(session, active)
        return {
            "conversation_id": row.id,
            "version": row.version,
            "history": state["history"],
            "expires_at": row.expires_at.isoformat(),
            "active_task_id": state.get("active_task_id"),
            "active_email_draft_id": email_draft.active_text_id(state),
            "active_proposal_id": state.get("proposal_id"),
            "proposal": proposal,
            "calendar_choices": event_choices.public(state),
            "pending_request_id": row.pending_request_id,
            "pending_recovery": (
                {
                    "active": bool(row.lease_until and row.lease_until > datetime.now(UTC)),
                    "has_saved_result": bool(state.get("pending_result")),
                }
                if row.pending_request_id
                else None
            ),
            "context_snapshot_id": state["refs"].get("selected", {}).get("context_id"),
        }


async def remove(owner, identifier, factory=None):
    factory = factory or get_session_factory()
    async with factory.begin() as session:
        await session.get(User, owner, with_for_update=True)
        row = await session.scalar(
            select(Conversation)
            .where(Conversation.id == identifier, Conversation.user_id == owner)
            .with_for_update()
        )
        if row is None:
            return {"deleted": True, "tasks_retained": True}
        if row.lease_until and row.lease_until > datetime.now(UTC):
            raise ApiError(
                409,
                "conversation_busy",
                "Wait for the current response to finish before deleting this conversation.",
            )
        await session.delete(row)
    return {"deleted": True, "tasks_retained": True}
