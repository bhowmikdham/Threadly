"""A finite master coordinator. Full-command review precedes every child workflow.

Model inference is outside transactions. A classifier may offer candidate labels,
but only validated clauses and exact user review select the installed kernel.
"""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.assistant import command_plans, scheduling, tasks, workflows
from app.assistant.source_data import context_data
from app.assistant.summary import digest
from app.assistant.summary import release_manifest as model_release
from app.db.models import CommandPlan
from app.model_client.providers import ProviderError
from app.model_client.structured import reject_duplicate_keys
from app.planner import command, scheduling_extraction
from app.schemas.assistant import AssistantRequest
from app.schemas.command_plan import CommandClause, CommandPlanRequest
from app.schemas.compound import CompoundRequest
from app.schemas.coordinator import CoordinatedCommand, CoordinatorRequest
from app.schemas.lookup_draft import LookupDraftRequest
from app.schemas.scheduling import SchedulingConstraints, SchedulingRequest
from app.schemas.scheduling_proposal import SchedulingProposalRequest
from app.schemas.workflow import WorkflowRequest
from app.workflows import auxiliary

RELEASE = "reviewed-master-workflow-1.0.0"
PROMPT = (
    """Interpret a complete command. Return JSON only with command, scheduling, other_operation.
command follows the whole-command contract below. scheduling is null unless schedule is
requested; otherwise supply the scheduling extraction contract below with EXACTLY the
same clauses as command. other_operation is help, transform_text, lookup_entity or
lookup_commitments for bounded
Other assistance, otherwise null. Never reduce send or book to drafting or checking.
Never discard requested steps or negations. Do not execute any operation.
"""
    + command.PROMPT
    + "\nScheduling extraction contract:\n"
    + scheduling_extraction.PROMPT
)


def release():
    return {
        "workflow": RELEASE,
        "model": model_release(),
        "auxiliary": auxiliary.release(),
        "execution": workflows.release_manifest(),
        "contract_hash": digest(
            {
                "prompt": PROMPT,
                "schema": CoordinatedCommand.model_json_schema(),
                "request": CoordinatorRequest.model_json_schema(),
                "command": command.contract_hash(),
                "scheduling": scheduling_extraction.contract_hash(),
                "policy": "finite-reviewed-master-v1",
            }
        ),
    }


async def owned(session, owner, identifier, *, lock=False):
    row = await command_plans.owned(session, owner, identifier, lock=lock)
    if row.release.get("workflow") != RELEASE:
        raise ApiError(404, "not_found", "Unknown master workflow proposal.")
    return row


async def reserve(session, owner, request):
    value, hashed = request.model_dump(), digest(request.model_dump())
    old = await session.scalar(
        select(CommandPlan).where(
            CommandPlan.user_id == owner, CommandPlan.request_id == request.request_id
        )
    )
    if old:
        if old.request_hash != hashed or old.release.get("workflow") != RELEASE:
            raise ApiError(409, "idempotency_conflict", "Proposal key already used.")
        return old, False
    context = await command_plans.source(session, owner, request)
    binding = None
    if request.expected_preferences_version is not None:
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
    elif request.anchor_message_id:
        raise ApiError(422, "calendar_preferences_required", "Select saved Calendar preferences.")
    now = await session.scalar(select(func.clock_timestamp()))
    identifier = str(uuid4())
    manifest = {
        **release(),
        "binding": binding,
        "compound_execution": {
            template: command_plans.execution_release(context, template)
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
            id=identifier,
            user_id=owner,
            request_id=request.request_id,
            request_hash=hashed,
            request=value,
            release=manifest,
            context_snapshot_id=request.context_snapshot_id,
            source_hash=digest(context_data(context)) if context else None,
            state="planning",
            expires_at=now + timedelta(minutes=15),
        )
        .on_conflict_do_nothing(constraint="uq_command_plan_request")
        .returning(CommandPlan.id)
    )
    if inserted:
        return await owned(session, owner, identifier), True
    return await reserve(session, owner, request)


def parse(text, instruction):
    if len(text) > 48000:
        raise ValueError("Master proposal too large")
    parsed = CoordinatedCommand.model_validate(
        json.loads(text, object_pairs_hook=reject_duplicate_keys)
    )
    command.parse(json.dumps(parsed.command.model_dump()), instruction)
    if parsed.scheduling:
        scheduling_extraction.parse(json.dumps(parsed.scheduling.model_dump()), instruction)
        if parsed.command.clauses != parsed.scheduling.clauses:
            raise ValueError("Inconsistent operation coverage")
    return parsed


def compile_request(row, parsed):
    request = CoordinatorRequest.model_validate(row.request)
    proposal = parsed.command
    requested = [op for c in proposal.clauses if c.kind == "requested" for op in c.operations]
    prohibited = {op for c in proposal.clauses if c.kind == "prohibited" for op in c.operations}
    result = {
        "clauses": [
            {**c.model_dump(), "text": command.span_text(request.instruction, c)}
            for c in proposal.clauses
        ],
        "requested_operations": requested,
        "prohibited_operations": sorted(prohibited),
        "questions": proposal.ambiguities,
        "compiled_request": None,
        "kernel": None,
        "external_actions": False,
        "reason": None,
    }
    if prohibited.intersection(requested) or proposal.ambiguities:
        return "needs_clarification", {**result, "reason": "ambiguous_complete_request"}
    if (
        not requested
        or len(requested) != len(set(requested))
        or set(requested) & {"send", "book", "search_mailbox"}
    ):
        return "unsupported", {**result, "reason": "complete_combination_not_installed"}
    if ("schedule" in requested) != bool(parsed.scheduling):
        raise ValueError("Missing or unexpected scheduling extraction")
    if ("other" in requested) != bool(parsed.other_operation):
        raise ValueError("Missing or unexpected Other operation")
    base = {
        "schema_version": "1.0",
        "request_id": f"master:{row.id}",
        "instruction": request.instruction,
        "context_snapshot_id": request.context_snapshot_id,
    }
    if requested == ["compose"] and not request.context_snapshot_id:
        if (
            not request.draft_options
            or not request.draft_options.to
            or request.draft_options.reply_message_id
        ):
            return "needs_clarification", {**result, "reason": "compose_recipients_required"}
        value = AssistantRequest(
            **base, intent_hint="compose", continuation=None, draft_options=request.draft_options
        )
        return "proposed", {**result, "kernel": "assistant", "compiled_request": value.model_dump()}
    if len(requested) == 2 and "schedule" not in requested:
        legacy_request = CommandPlanRequest.model_validate(
            {k: v for k, v in row.request.items() if k in CommandPlanRequest.model_fields}
        )
        state, compiled = command.compile_plan(legacy_request, proposal, row.id)
        return state, {
            **result,
            "kernel": "compound",
            "compiled_request": compiled["compiled_request"],
            "reason": compiled["reason"],
            "missing_fields": compiled["missing_fields"],
        }
    if requested == ["other"] and parsed.other_operation in {"lookup_entity", "lookup_commitments"}:
        if not request.context_snapshot_id:
            return "needs_clarification", {**result, "reason": "source_context_required"}
        value = WorkflowRequest(**base, operations=[parsed.other_operation])
        return "proposed", {**result, "kernel": "workflow", "compiled_request": value.model_dump()}
    if requested in (["other"], ["search_capture"]):
        options = {"operation": parsed.other_operation or "search_mail"}
        if requested == ["search_capture"]:
            if not proposal.lookup_query:
                return "needs_clarification", {**result, "reason": "literal_query_required"}
            options["query"] = command.span_text(request.instruction, proposal.lookup_query).strip(
                "\"'"
            )
        if parsed.other_operation == "transform_text":
            if not request.transform_message_id:
                return "needs_clarification", {**result, "reason": "message_selection_required"}
            options["message_id"] = request.transform_message_id
        if parsed.other_operation == "help":
            base["context_snapshot_id"] = None
        value = AssistantRequest(
            **base, intent_hint="other", continuation=None, read_options=options
        )
        return "proposed", {**result, "kernel": "read", "compiled_request": value.model_dump()}
    allowed = [
        {"summary"},
        {"reply"},
        {"compose"},
        {"plan"},
        {"schedule"},
        {"schedule", "reply"},
        {"schedule", "compose"},
        {"summary", "schedule", "reply"},
        {"summary", "schedule", "compose"},
    ]
    if set(requested) not in allowed:
        return "unsupported", {**result, "reason": "complete_combination_not_installed"}
    if not request.context_snapshot_id and requested != ["schedule"]:
        return "needs_clarification", {**result, "reason": "source_context_required"}
    schedule = None
    if "schedule" in requested:
        binding = row.release.get("binding")
        if binding is None:
            return "needs_clarification", {**result, "reason": "calendar_preferences_required"}
        # The master has accounted for ALL operations. Delegate only literal constraint
        # normalization, without pretending the original command was schedule-only.
        extracted = parsed.scheduling.model_copy(deep=True)
        extracted.clauses = [
            CommandClause(
                start=c.start,
                end=c.end,
                kind="requested"
                if c.kind == "requested" and "schedule" in c.operations
                else "context",
                operations=["schedule"]
                if c.kind == "requested" and "schedule" in c.operations
                else [],
            )
            for c in extracted.clauses
        ]
        seed = SchedulingProposalRequest(
            schema_version="1.0",
            request_id=request.request_id,
            instruction=request.instruction,
            expected_preferences_version=request.expected_preferences_version,
            context_snapshot_id=request.context_snapshot_id,
            anchor_message_id=request.anchor_message_id,
        )
        state, compiled = scheduling_extraction.compile_proposal(seed, extracted, row.id, binding)
        if state != "proposed" or compiled.get("pending_fields"):
            return "needs_clarification", {
                **result,
                "reason": compiled.get("reason") or "scheduling_fields_required",
            }
        schedule = {
            k: compiled["compiled_request"][k]
            for k in (
                "operation",
                "constraints",
                "expected_preferences_version",
                "anchor_message_id",
            )
        }
        result["scheduling_assumptions"] = compiled["assumptions"]
        if not request.context_snapshot_id and requested == ["schedule"]:
            standalone = SchedulingRequest.model_validate(compiled["compiled_request"])
            standalone = standalone.model_copy(update={"request_id": base["request_id"]})
            return "proposed", {
                **result,
                "kernel": "scheduling",
                "compiled_request": standalone.model_dump(),
            }
    if (
        "summary" in requested
        and set(requested) & {"reply", "compose"}
        and proposal.summary_usage not in {"include", "separate"}
    ):
        return "needs_clarification", {**result, "reason": "summary_usage_required"}
    operations = [
        op for op in ("summary", "plan", "schedule", "reply", "compose") if op in requested
    ]
    operations = [{"reply": "draft_reply", "compose": "draft_new"}.get(op, op) for op in operations]
    try:
        compiled = WorkflowRequest(
            **base,
            operations=operations,
            schedule=schedule,
            draft_options=request.draft_options if set(requested) & {"reply", "compose"} else None,
            summary_in_draft=proposal.summary_usage == "include",
        )
    except ValueError:
        return "needs_clarification", {**result, "reason": "workflow_inputs_required"}
    return "proposed", {**result, "kernel": "workflow", "compiled_request": compiled.model_dump()}


async def interpret(row, model=None):
    try:
        if any(row.release.get(k) != v for k, v in release().items()):
            return "failed", {"reason": "release_unavailable"}
        instruction = row.request["instruction"]
        prompt = (
            PROMPT
            + "\nWORDS_JSON:\n"
            + json.dumps(
                [
                    {"number": n, "text": w.group()}
                    for n, w in enumerate(command.words(instruction), 1)
                ]
            )
        )
        async with asyncio.timeout(45):
            text, provenance = await auxiliary.generate(
                "route_command", prompt, row.release["auxiliary"], model=model, max_tokens=4000
            )
        proposal = parse(text, instruction)
        state, result = compile_request(row, proposal)
        return state, {
            **result,
            "proposal": proposal.model_dump(),
            "provenance": provenance,
        }
    except (ProviderError, TimeoutError):
        return "failed", {"reason": "upstream_model_unavailable"}
    except (ValueError, TypeError, KeyError):
        return "failed", {"reason": "invalid_master_proposal"}
    except Exception:
        return "failed", {"reason": "master_planning_failed"}


async def confirm(session, owner, identifier, confirmation):
    # Account/preferences locks precede proposal/task locks on scheduling paths.
    initial = await owned(session, owner, identifier)
    if initial.state != "consumed" and initial.release.get("binding"):
        await scheduling.check_current(
            session, owner, initial.release["binding"], initial.context_snapshot_id
        )
    row = await owned(session, owner, identifier, lock=True)
    if confirmation.plan_hash != row.plan_hash or row.plan_hash != command_plans.result_hash(
        row, row.result
    ):
        raise ApiError(409, "plan_changed", "Review the exact saved workflow.")
    if row.state == "consumed":
        return await tasks.owned_task(session, owner, row.task_id)
    if row.state != "proposed" or row.expires_at <= await session.scalar(
        select(func.clock_timestamp())
    ):
        raise ApiError(
            409, "plan_not_confirmable", "Resolve all inputs and create a fresh proposal."
        )
    if any(row.release.get(k) != v for k, v in release().items()):
        raise ApiError(
            409, "release_unavailable", "Replan with the current workflow configuration."
        )
    request = CoordinatorRequest.model_validate(row.request)
    parsed = parse(json.dumps(row.result["proposal"]), request.instruction)
    state, result = compile_request(row, parsed)
    if state != "proposed" or result != {
        k: v for k, v in row.result.items() if k not in {"proposal", "provenance"}
    }:
        raise ApiError(409, "plan_changed", "The complete workflow needs review.")
    context = await command_plans.source(session, owner, request)
    if (digest(context_data(context)) if context else None) != row.source_hash:
        raise ApiError(409, "command_source_changed", "Capture current source and replan.")
    value = result["compiled_request"]
    if result["kernel"] == "workflow":
        workflow = WorkflowRequest.model_validate(value)
        task = await tasks.submit(session, owner, workflow.as_request(), workflow=workflow)
    elif result["kernel"] == "scheduling":
        schedule = SchedulingRequest.model_validate(value)
        binding = {**row.release["binding"], "request": schedule.model_dump()}
        task = await tasks.submit(
            session, owner, schedule.as_request(), schedule=schedule, schedule_binding=binding
        )
    elif result["kernel"] in {"read", "assistant"}:
        task = await tasks.submit(session, owner, AssistantRequest.model_validate(value))
    else:
        compound = (
            LookupDraftRequest if value["template"].startswith("lookup") else CompoundRequest
        ).model_validate(value)
        if row.release["compound_execution"][compound.template] != command_plans.execution_release(
            context, compound.template
        ):
            raise ApiError(
                409, "release_unavailable", "Replan with the current compound configuration."
            )
        task = await tasks.submit(session, owner, compound.as_request(), compound=compound)
    row.state, row.task_id = "consumed", task.id
    await session.flush()
    return task
