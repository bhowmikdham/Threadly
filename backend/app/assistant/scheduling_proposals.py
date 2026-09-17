"""Durable one-call interpretation followed by exact, explicitly reviewed dispatch."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.assistant import scheduling, tasks
from app.assistant.summary import digest
from app.assistant.summary import release_manifest as model_release
from app.db.models import ContextSnapshot, SchedulingProposal
from app.model_client.client import get_model_client
from app.model_client.providers import ProviderError
from app.planner import scheduling_extraction as extraction
from app.schemas.scheduling import SchedulingConstraints, SchedulingRequest
from app.schemas.scheduling_proposal import SchedulingProposalRequest

EXPIRY_MINUTES = 15


def release():
    return {
        "workflow": extraction.RELEASE,
        "contract_hash": extraction.contract_hash(),
        "model": model_release(),
        "execution": scheduling.release_manifest(),
    }


async def owned(session, owner, proposal_id, *, lock=False):
    query = select(SchedulingProposal).where(
        SchedulingProposal.id == proposal_id, SchedulingProposal.user_id == owner
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await session.scalar(query)
    if result is None:
        raise ApiError(404, "not_found", "Unknown scheduling proposal.")
    return result


async def reserve(session, owner, request):
    value, hashed = request.model_dump(), digest(request.model_dump())
    existing = await session.scalar(
        select(SchedulingProposal).where(
            SchedulingProposal.user_id == owner, SchedulingProposal.request_id == request.request_id
        )
    )
    if existing:
        if existing.request_hash != hashed:
            raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
        return existing, False
    context = None
    if request.context_snapshot_id:
        context = await session.scalar(
            select(ContextSnapshot).where(
                ContextSnapshot.id == request.context_snapshot_id, ContextSnapshot.user_id == owner
            )
        )
        if context is None:
            raise ApiError(404, "context_not_found", "Select an accessible saved capture.")
    # This placeholder only captures trusted account, preferences and temporal anchor.
    # It is never dispatched and does not represent an inferred operation.
    seed = SchedulingRequest(
        schema_version="1.0",
        request_id=request.request_id,
        operation="suggest_slots",
        expected_preferences_version=request.expected_preferences_version,
        context_snapshot_id=request.context_snapshot_id,
        anchor_message_id=request.anchor_message_id,
        constraints=SchedulingConstraints(),
    )
    binding = await scheduling.bind_input(session, owner, seed, context)
    now = await session.scalar(select(func.clock_timestamp()))
    proposal_id = str(uuid4())
    inserted = await session.scalar(
        insert(SchedulingProposal)
        .values(
            id=proposal_id,
            user_id=owner,
            request_id=request.request_id,
            request_hash=hashed,
            request=value,
            context_snapshot_id=request.context_snapshot_id,
            binding=binding,
            source_hash=binding["source_hash"],
            release=release(),
            state="planning",
            expires_at=now + timedelta(minutes=EXPIRY_MINUTES),
        )
        .on_conflict_do_nothing(constraint="uq_scheduling_proposal_request")
        .returning(SchedulingProposal.id)
    )
    if inserted:
        return await owned(session, owner, proposal_id), True
    existing = await session.scalar(
        select(SchedulingProposal).where(
            SchedulingProposal.user_id == owner, SchedulingProposal.request_id == request.request_id
        )
    )
    if existing.request_hash != hashed:
        raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")
    return existing, False


async def interpret(proposal, model=None):
    """Reservation MUST be committed before inference. No automatic retry/fallback."""
    try:
        if proposal.release != release():
            return "failed", {"reason": "release_unavailable"}
        request = SchedulingProposalRequest.model_validate(proposal.request)
        async with asyncio.timeout(extraction.TIMEOUT_SECONDS):
            text, info = await (model or get_model_client()).generate(
                extraction.prompt(request.instruction), max_tokens=extraction.MAX_TOKENS
            )
        parsed = extraction.parse(text, request.instruction)
        state, result = extraction.compile_proposal(request, parsed, proposal.id, proposal.binding)
        return state, {
            **result,
            "extraction": parsed.model_dump(),
            "provenance": {"provider": info.provider, "model": info.model},
        }
    except (ProviderError, TimeoutError):
        return "failed", {"reason": "upstream_model_unavailable"}
    except (ValueError, TypeError, KeyError):
        return "failed", {"reason": "invalid_scheduling_extraction"}
    except Exception:
        return "failed", {"reason": "scheduling_extraction_failed"}


def result_hash(proposal, result):
    return digest(
        {
            "id": proposal.id,
            "owner": proposal.user_id,
            "request": proposal.request,
            "binding": proposal.binding,
            "release": proposal.release,
            "result": result,
        }
    )


async def complete(session, owner, proposal_id, state, result):
    proposal = await owned(session, owner, proposal_id, lock=True)
    if proposal.state != "planning":
        return proposal
    if await session.scalar(select(func.clock_timestamp())) >= proposal.expires_at:
        proposal.state = "expired"
    else:
        proposal.state, proposal.result = state, result
        proposal.plan_hash = result_hash(proposal, result)
    await session.flush()
    return proposal


async def view(session, proposal):
    now = await session.scalar(select(func.clock_timestamp()))
    expired = now >= proposal.expires_at and proposal.state in {"planning", "proposed"}
    return {
        "proposal_id": proposal.id,
        "state": "expired" if expired else proposal.state,
        "request": proposal.request,
        "result": proposal.result,
        "proposal_hash": proposal.plan_hash,
        "release": proposal.release,
        "expires_at": proposal.expires_at.isoformat(),
        "task_id": proposal.task_id,
        "requires_complete_request_review": True,
        "external_actions": False,
        "confirmation_revalidates_source_and_preferences": True,
    }


async def confirm(session, owner, proposal_id, confirmation):
    # Read immutable inputs, then use the Calendar lock order: account, preferences,
    # source, proposal, task. Concurrent confirmations refresh after taking the lock.
    proposal = await owned(session, owner, proposal_id)
    if confirmation.proposal_hash != proposal.plan_hash or proposal.plan_hash != result_hash(
        proposal, proposal.result
    ):
        raise ApiError(
            409, "proposal_changed", "Reload and review the complete scheduling proposal."
        )
    if proposal.state == "consumed":
        return await tasks.owned_task(session, owner, proposal.task_id)
    await scheduling.check_current(session, owner, proposal.binding, proposal.context_snapshot_id)
    proposal = await owned(session, owner, proposal_id, lock=True)
    if proposal.state == "consumed":
        return await tasks.owned_task(session, owner, proposal.task_id)
    now = await session.scalar(select(func.clock_timestamp()))
    if proposal.state != "proposed" or now >= proposal.expires_at:
        raise ApiError(
            409, "proposal_not_confirmable", "Create a new proposal with resolved inputs."
        )
    current = release()
    if any(proposal.release[k] != current[k] for k in ("workflow", "contract_hash", "execution")):
        raise ApiError(
            409, "release_unavailable", "Create and review a current scheduling proposal."
        )
    request = SchedulingProposalRequest.model_validate(proposal.request)
    parsed = extraction.parse(json.dumps(proposal.result["extraction"]), request.instruction)
    state, compiled = extraction.compile_proposal(request, parsed, proposal.id, proposal.binding)
    expected = {k: v for k, v in proposal.result.items() if k not in {"extraction", "provenance"}}
    if state != "proposed" or compiled != expected:
        raise ApiError(
            409, "proposal_changed", "The saved interpretation no longer matches its input."
        )
    schedule = SchedulingRequest.model_validate(compiled["compiled_request"])
    binding = {**proposal.binding, "request": schedule.model_dump()}
    task = await tasks.submit(
        session, owner, schedule.as_request(), schedule=schedule, schedule_binding=binding
    )
    if task.release != proposal.release["execution"] or task.scheduling_input != binding:
        raise ApiError(
            409, "proposal_changed", "The resulting task does not match the reviewed proposal."
        )
    proposal.state, proposal.task_id = "consumed", task.id
    await session.flush()
    return task
