"""Stateless proposal routing. No tools, context reads, approvals or execution."""

import json
import re

from pydantic import ValidationError

from app.api.errors import ApiError
from app.model_client.client import ModelClient, get_model_client
from app.model_client.providers import ProviderError
from app.model_client.structured import reject_duplicate_keys
from app.planner.intent_prompt import ROUTER_VERSION, routing_prompt
from app.schemas.assistant import (
    RouteDecision,
    RouteParameters,
    RoutePreview,
    RoutePreviewRequest,
)

# Full matches only: broad keyword rules lose secondary intents and negations.
_EXACT_COMMANDS = {
    "summarise this": ("summarise", "summary", ["summarise_thread"]),
    "summarize this": ("summarise", "summary", ["summarise_thread"]),
    "summarise this thread": ("summarise", "summary", ["summarise_thread"]),
    "summarize this thread": ("summarise", "summary", ["summarise_thread"]),
    "summarise this email": ("summarise", "summary", ["summarise_thread"]),
    "summarize this email": ("summarise", "summary", ["summarise_thread"]),
    "draft a reply": ("reply", "draft", ["draft_reply"]),
    "draft a reply; do not send it": ("reply", "draft", ["draft_reply"]),
    "write an email": ("compose", "draft", ["draft_new"]),
    "turn these requests into a work plan": ("plan_schedule", "plan", ["plan_actions"]),
    "what can you do": ("other", "answer", ["help"]),
    "help": ("other", "answer", ["help"]),
}

_PRIMARY_OPERATIONS = {
    "summarise": {"summarise_thread"},
    "plan_schedule": {"plan_actions", "check_time", "suggest_slots"},
    "reply": {"draft_reply"},
    "compose": {"draft_new"},
    "other": {"lookup_entity", "search_mail", "lookup_commitments", "transform_text", "help"},
}

_COMBINATIONS = {
    ("summarise_thread", "draft_reply"),
    ("plan_actions", "draft_reply"),
    ("check_time", "draft_reply"),
    ("suggest_slots", "draft_reply"),
    ("lookup_entity", "draft_reply"),
    ("lookup_entity", "draft_new"),
    ("search_mail", "draft_reply"),
    ("search_mail", "draft_new"),
    ("lookup_commitments", "draft_reply"),
}


def parse_decision(text: str) -> RouteDecision:
    """No markdown stripping, arbitrary JSON extraction or permissive coercion."""
    if len(text) > 16000:
        raise ValueError("route response exceeds the size budget")
    value = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    decision = RouteDecision.model_validate(value)
    if decision.context_snapshot_id is not None or decision.parameters.recipient_refs:
        raise ValueError("model invented unbound context or recipient references")
    if len(decision.operations) > 1 and tuple(decision.operations) not in _COMBINATIONS:
        raise ValueError("unsupported operation sequence")
    if decision.operations and not (
        set(decision.operations) & _PRIMARY_OPERATIONS[decision.intent]
    ):
        raise ValueError("primary intent does not match the proposed operations")
    if set(decision.operations) & {"check_time", "suggest_slots"}:
        if decision.intent != "plan_schedule":
            raise ValueError("calendar requests must use plan_schedule")
    if (
        decision.status == "ready"
        and not decision.operations
        and decision.requested_action == "none"
    ):
        raise ValueError("fresh ready routes require an operation or action proposal")
    return decision


def require_context(
    decision: RouteDecision,
    *,
    has_source: bool = False,
    has_reply_target: bool = False,
    has_recipients: bool = False,
) -> RouteDecision:
    """Deterministic preconditions; a snapshot binds source text, never a reply/action target."""
    if decision.status == "unsupported":
        return decision
    missing = [
        field
        for field in decision.missing_fields
        if not (
            (has_source and field == "source_context")
            or (has_reply_target and field == "reply_target")
            or (has_recipients and field == "recipient")
        )
    ]
    operations = set(decision.operations)
    if not has_source and operations & {"summarise_thread", "plan_actions", "transform_text"}:
        missing.append("source_context")
    if "draft_reply" in operations and not has_reply_target:
        missing.append("reply_target")
    if "draft_new" in operations and not has_recipients:
        missing.append("recipient")
    if operations & {"check_time", "suggest_slots"}:
        missing.append("timezone")
        if decision.parameters.duration_minutes is None:
            missing.append("duration_minutes")
        if not decision.parameters.date_phrase:
            missing.append("date_range")
        if "check_time" in operations:
            phrase = decision.parameters.time_phrase
            if not phrase:
                missing.append("time")
            elif re.fullmatch(r"\s*(?:at\s+)?(?:[1-9]|1[0-2])(?::[0-5][0-9])?\s*", phrase):
                missing.append("am_or_pm")
    if decision.requested_action != "none":
        missing.append("saved_action_target")
    if missing:
        missing = list(dict.fromkeys(missing))
        value = decision.model_dump()
        value.update(
            status="needs_clarification",
            missing_fields=missing,
            clarification="Please provide or select: " + ", ".join(missing) + ".",
        )
        return RouteDecision.model_validate(value)
    if decision.status == "needs_clarification":
        value = decision.model_dump()
        value.update(status="ready", missing_fields=[], clarification=None)
        return RouteDecision.model_validate(value)
    return decision


async def propose_route(
    request: RoutePreviewRequest, model: ModelClient | None = None, *, policy_suffix: str = ""
) -> tuple[RouteDecision, str, dict | None]:
    normalized = request.instruction.strip().casefold().rstrip(".!?")
    command = _EXACT_COMMANDS.get(normalized)
    # Conflicting hints are sent to the model, never allowed to force a rule.
    if command and request.intent_hint in (None, command[0]):
        intent, output, operations = command
        decision = RouteDecision(
            schema_version="1.0",
            status="ready",
            intent=intent,
            output_kind=output,
            operations=operations,
            context_snapshot_id=None,
            parameters=RouteParameters.empty(),
            missing_fields=[],
            clarification=None,
            rationale="Exact supported user command.",
            requested_action="none",
        )
        source, provenance = "rule", None
    else:
        try:
            text, _info = await (model or get_model_client()).generate(
                routing_prompt(request, policy_suffix=policy_suffix), small=True, max_tokens=1200
            )
            decision = parse_decision(text)
        except ProviderError:
            raise ApiError(
                503, "upstream_model_unavailable", "Intent routing is unavailable."
            ) from None
        except (ValidationError, ValueError, TypeError):
            # One attempt, no guessed fallback and no raw model output in errors.
            raise ApiError(
                502, "invalid_route_output", "The model returned an invalid route."
            ) from None
        source = "model"
        provenance = {"provider": _info.provider, "model": _info.model}
    return decision, source, provenance


async def preview_route(
    request: RoutePreviewRequest, model: ModelClient | None = None
) -> RoutePreview:
    decision, source, _ = await propose_route(request, model)
    return RoutePreview(
        decision=require_context(decision),
        router_version=ROUTER_VERSION,
        source=source,
    )
