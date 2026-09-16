"""Persist a bounded interpretation, then explicitly confirm one pinned compound task."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.assistant import drafting, lookup_draft, reads, steps, summary_quality, tasks, ui_routing
from app.assistant.summary import digest
from app.assistant.summary import release_manifest as model_release
from app.db.models import CommandPlan, ContextSnapshot, User
from app.model_client.client import get_model_client
from app.model_client.providers import ProviderError
from app.planner import command
from app.schemas.command_plan import CommandPlanRequest
from app.schemas.compound import CompoundRequest
from app.schemas.lookup_draft import LookupDraftRequest
from app.workflows import registry

EXPIRY_MINUTES = 15


def planner_release():
    return {
        "workflow": command.RELEASE,
        "contract_hash": command.contract_hash(),
        "model": model_release(),
    }


def execution_release(context, template):
    base = summary_quality.wrap_release(registry.release_manifest())
    if context and context.payload.get("schema_version") == "1.1":
        base = ui_routing.wrap_release(base)
    return (lookup_draft if template.startswith("lookup") else steps).wrap_release(base)


async def owned(session, owner, plan_id, *, lock=False):
    q = select(CommandPlan).where(CommandPlan.id == plan_id, CommandPlan.user_id == owner)
    if lock:
        q = q.with_for_update()
    plan = await session.scalar(q)
    if plan is None:
        raise ApiError(404, "not_found", "Unknown command plan.")
    return plan


async def source(session, owner, request):
    context = None
    if request.context_snapshot_id:
        context = await session.scalar(
            select(ContextSnapshot).where(
                ContextSnapshot.id == request.context_snapshot_id, ContextSnapshot.user_id == owner
            )
        )
        if context is None:
            raise ApiError(404, "context_not_found", "Select an accessible saved capture.")
        if not context.payload.get("messages"):
            raise ApiError(409, "context_empty", "Select a nonempty capture.")
        await reads.validate_source(session, owner, context.payload, "search_mail")
    user = await session.get(User, owner)
    if user is None:
        raise ApiError(401, "unauthorized", "Account no longer exists.")
    if request.draft_options and (context or not request.draft_options.reply_message_id):
        await drafting.bind_input(session, user, request.draft_options, context)
    return context


async def reserve(session, owner, request):
    value = request.model_dump()
    hashed = digest(value)
    existing = await session.scalar(
        select(CommandPlan).where(
            CommandPlan.user_id == owner, CommandPlan.request_id == request.request_id
        )
    )
    if existing:
        if existing.request_hash != hashed:
            raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
        return existing, False
    context = await source(session, owner, request)
    now = await session.scalar(select(func.clock_timestamp()))
    plan_id = str(uuid4())
    # Pin every installed kernel before inference; output never chooses a resource ID.
    release = {
        **planner_release(),
        "execution": {
            template: execution_release(context, template)
            for template in (
                "summary_then_reply",
                "summary_then_compose",
                "lookup_then_reply",
                "lookup_then_compose",
            )
        },
    }
    inserted = await session.scalar(
        insert(CommandPlan)
        .values(
            id=plan_id,
            user_id=owner,
            request_id=request.request_id,
            request_hash=hashed,
            request=value,
            context_snapshot_id=request.context_snapshot_id,
            source_hash=digest(context.payload) if context else None,
            release=release,
            state="planning",
            expires_at=now + timedelta(minutes=EXPIRY_MINUTES),
        )
        .on_conflict_do_nothing(constraint="uq_command_plan_request")
        .returning(CommandPlan.id)
    )
    if inserted:
        return await owned(session, owner, plan_id), True
    existing = await session.scalar(
        select(CommandPlan).where(
            CommandPlan.user_id == owner, CommandPlan.request_id == request.request_id
        )
    )
    if existing.request_hash != hashed:
        raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
    return existing, False


async def interpret(plan, model=None):
    """Caller must commit reservation before this bounded, one-call operation."""
    try:
        if any(plan.release.get(k) != v for k, v in planner_release().items()):
            return "failed", {"reason": "release_unavailable"}
        request = CommandPlanRequest.model_validate(plan.request)
        async with asyncio.timeout(command.TIMEOUT_SECONDS):
            text, info = await (model or get_model_client()).generate(
                command.prompt(request.instruction), max_tokens=3000
            )
        proposal = command.parse(text, request.instruction)
        state, result = command.compile_plan(request, proposal, plan.id)
        return state, {
            **result,
            "proposal": proposal.model_dump(),
            "provenance": {"provider": info.provider, "model": info.model},
        }
    except (ProviderError, TimeoutError):
        return "failed", {"reason": "upstream_model_unavailable"}
    except ApiError as exc:
        return "failed", {
            "reason": "upstream_model_unavailable"
            if exc.code == "upstream_model_unavailable"
            else "command_planning_failed"
        }
    except (ValueError, TypeError, KeyError):
        return "failed", {"reason": "invalid_command_plan_output"}
    except Exception:
        return "failed", {"reason": "command_planning_failed"}


def result_hash(plan, result):
    return digest(
        {
            "id": plan.id,
            "owner": plan.user_id,
            "request": plan.request,
            "source_hash": plan.source_hash,
            "release": plan.release,
            "result": result,
        }
    )


async def complete(session, owner, plan_id, state, result):
    plan = await owned(session, owner, plan_id, lock=True)
    if plan.state != "planning":
        return plan
    now = await session.scalar(select(func.clock_timestamp()))
    if now >= plan.expires_at:
        plan.state = "expired"
        return plan
    plan.state, plan.result = state, result
    plan.plan_hash = result_hash(plan, result)
    await session.flush()
    return plan


async def view(session, plan):
    now = await session.scalar(select(func.clock_timestamp()))
    expired = now >= plan.expires_at and plan.state in {"planning", "proposed"}
    return {
        "plan_id": plan.id,
        "state": "expired" if expired else plan.state,
        "request": plan.request,
        "result": plan.result,
        "plan_hash": plan.plan_hash,
        "release": plan.release,
        "expires_at": plan.expires_at.isoformat(),
        "task_id": plan.task_id,
        "external_actions": False,
        "requires_complete_command_review": True,
    }


async def confirm(session, owner, plan_id, confirmation):
    plan = await owned(session, owner, plan_id, lock=True)
    if confirmation.plan_hash != plan.plan_hash or plan.plan_hash != result_hash(plan, plan.result):
        raise ApiError(409, "plan_changed", "Reload and review the complete saved plan.")
    if plan.state == "consumed":
        return await tasks.owned_task(session, owner, plan.task_id)
    now = await session.scalar(select(func.clock_timestamp()))
    if plan.state != "proposed" or now >= plan.expires_at:
        raise ApiError(409, "plan_not_confirmable", "Create a new plan with resolved inputs.")
    if (
        plan.release.get("workflow") != command.RELEASE
        or plan.release.get("contract_hash") != command.contract_hash()
    ):
        raise ApiError(409, "release_unavailable", "Create and review a current command plan.")
    request = CommandPlanRequest.model_validate(plan.request)
    proposal = command.parse(json.dumps(plan.result["proposal"]), request.instruction)
    state, compiled = command.compile_plan(request, proposal, plan.id)
    expected = {k: v for k, v in plan.result.items() if k not in {"proposal", "provenance"}}
    if state != "proposed" or compiled != expected:
        raise ApiError(409, "plan_changed", "The saved interpretation no longer matches its input.")
    context = await source(session, owner, request)
    if digest(context.payload) != plan.source_hash:
        raise ApiError(409, "command_source_changed", "Capture the changed source and replan.")
    value = compiled["compiled_request"]
    schema = LookupDraftRequest if value["template"].startswith("lookup") else CompoundRequest
    compound = schema.model_validate(value)
    pinned = plan.release["execution"][compound.template]
    if execution_release(context, compound.template) != pinned:
        raise ApiError(409, "release_unavailable", "Workflow configuration changed; replan first.")
    task = await tasks.submit(session, owner, compound.as_request(), compound=compound)
    if task.release != pinned:
        raise ApiError(409, "release_unavailable", "Workflow configuration changed; replan first.")
    plan.state, plan.task_id = "consumed", task.id
    await session.flush()
    return task
