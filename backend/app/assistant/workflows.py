"""Finite, source-fenced MVP graphs on the existing durable task/step kernel."""

import asyncio
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import ApiError
from app.assistant import drafting, planning, reads, scheduling, steps, summary_quality, tasks
from app.assistant.source_data import context_data
from app.assistant.summary import digest
from app.calendar import slots
from app.db.models import ArtifactRevision, AssistantJob, AssistantStep, CalendarSlotRequest
from app.model_client.client import get_model_client
from app.model_client.providers import ProviderError
from app.schemas.calendar import Preferences
from app.schemas.scheduling import SchedulingRequest
from app.schemas.workflow import WorkflowRequest
from app.workflows import auxiliary, registry
from app.workflows.bedrock_flows import FlowError, FlowInvoker

RELEASE = "mvp-workflow-1.0.0"
TIMEOUT = 120
SLOT_DRAFT_POLICY = "literal-calendar-options-in-separate-block-1.0.0"


def release_manifest():
    return {
        "workflow": RELEASE,
        "contract_hash": digest(
            {
                "schema": WorkflowRequest.model_json_schema(),
                "plan": planning.contract_hash(),
                "draft": SLOT_DRAFT_POLICY,
                "timeout": TIMEOUT,
            }
        ),
        "generation": summary_quality.wrap_release(registry.release_manifest()),
        "scheduling": scheduling.release_manifest(),
        "auxiliary": auxiliary.release(),
    }


async def bind_input(session, owner, request, context, envelope):
    if context is None or not context_data(context).get("messages"):
        raise ApiError(409, "context_empty", "Capture a nonempty thread.")
    plan = None
    if request.accepted_plan_artifact_id:
        plan = await planning.accepted_plan(
            session, owner, request.accepted_plan_artifact_id, lock=True
        )
        if plan.payload["context_snapshot_id"] != context.id:
            raise ApiError(
                409, "plan_context_mismatch", "Use the context saved with the accepted plan."
            )
    binding = None
    if request.schedule:
        schedule = SchedulingRequest(
            schema_version="1.0",
            request_id=request.request_id,
            context_snapshot_id=context.id,
            **request.schedule.model_dump(),
        )
        binding = await scheduling.bind_input(session, owner, schedule, context)
        question = scheduling.missing_question(binding, schedule.constraints)
        if not question:
            body = scheduling.query_body("preflight", 0, binding, schedule.constraints)
            resolution = slots.prepare(
                body,
                Preferences.model_validate(binding["preferences"]),
                datetime.fromisoformat(binding["anchor_at"]),
                await session.scalar(select(func.clock_timestamp())),
            )
            if resolution["state"] == "needs_clarification":
                question = scheduling.resolution_question(resolution)
        if question:
            raise ApiError(
                409,
                "workflow_clarification_required",
                "Resolve the scheduling question before starting the complete workflow.",
                question,
            )
    choice_slot = None
    if request.meeting_choice:
        choice_slot = await check_choice(session, owner, request)
    await reads.validate_source(session, owner, context_data(context), "search_mail")
    if request.draft_options and (not envelope or not envelope.get("to")):
        raise ApiError(409, "draft_recipients_missing", "Select draft recipients first.")
    return {
        "request": request.model_dump(),
        "source_hash": digest(context_data(context)),
        "schedule": binding,
        "choice_slot": choice_slot,
        "plan": {"artifact_id": plan.id, "payload_hash": digest(plan.payload)} if plan else None,
    }


async def current(session, claim):
    from app.operations.status import blocked_intents

    if blocked_intents(claim):
        raise ApiError(503, "intent_disabled", "This workflow is paused by the operator.")
    saved = claim.workflow_input
    if saved.get("plan"):
        plan = await planning.accepted_plan(
            session, claim.user_id, saved["plan"]["artifact_id"], lock=True
        )
        if digest(plan.payload) != saved["plan"]["payload_hash"]:
            raise ApiError(409, "accepted_plan_changed", "Reload the accepted plan.")
    if saved["schedule"]:
        await scheduling.check_current(session, claim.user_id, saved["schedule"], claim.context_id)
    request = WorkflowRequest.model_validate(saved["request"])
    if request.meeting_choice:
        slot = await check_choice(session, claim.user_id, request)
        if slot != saved["choice_slot"]:
            raise ApiError(409, "meeting_offer_changed", "Review the updated offer.")
    await reads.validate_source(session, claim.user_id, claim.snapshot, "search_mail")
    if digest(claim.snapshot) != saved["source_hash"]:
        raise ApiError(
            409, "workflow_source_changed", "Capture current source and start a new workflow."
        )


async def calendar_result(session, claim, query_id):
    row = await session.scalar(
        select(CalendarSlotRequest).where(
            CalendarSlotRequest.id == query_id, CalendarSlotRequest.user_id == claim.user_id
        )
    )
    if row is None:
        raise ApiError(409, "calendar_result_missing", "Recheck Calendar availability.")
    saved = claim.workflow_input["schedule"]
    request = SchedulingRequest.model_validate(saved["request"])
    body = scheduling.query_body(claim.task_id, 0, saved, request.constraints)
    if row.request != body.model_dump(mode="json"):
        raise ApiError(
            409, "calendar_query_mismatch", "Calendar result is not bound to this workflow."
        )
    await slots.check_current(session, claim.user_id, row)
    if row.state != "complete":
        raise ApiError(
            409,
            "calendar_coverage_unknown",
            "Cannot draft available times without complete Calendar evidence.",
        )
    return slots.view(row)


async def checkpoint(
    factory, claim, ordinal, operation, dependencies, payload=None, provenance=None
):
    async with factory.begin() as session:
        await current(session, claim)  # Account/preferences/source precede the task lock.
        # Every dependent publication/replay checks the original Calendar receipt.
        for artifact in dependencies:
            if artifact.payload.get("kind") in {"schedule_options", "availability"}:
                await calendar_result(
                    session, claim, artifact.payload["content"]["slot_request_id"]
                )
        task = await steps.fenced_task(session, claim)
        if task is None:
            return False, None
        hashed = digest(
            {
                "input": claim.workflow_input,
                "release": claim.release,
                "ordinal": ordinal,
                "dependencies": [{"id": a.id, "hash": digest(a.payload)} for a in dependencies],
            }
        )
        step = await session.get(AssistantStep, (task.id, ordinal))
        if step is None:
            step = AssistantStep(
                task_id=task.id,
                user_id=task.user_id,
                ordinal=ordinal,
                operation=operation,
                input_hash=hashed,
                release=claim.release,
                state="pending",
                attempts=0,
            )
            session.add(step)
        if (step.input_hash, step.operation, step.release) != (hashed, operation, claim.release):
            raise ApiError(
                409, "workflow_checkpoint_changed", "Start a new workflow for changed inputs."
            )
        if step.state == "succeeded":
            stored = await session.scalar(
                select(ArtifactRevision).where(
                    ArtifactRevision.id == step.artifact_id,
                    ArtifactRevision.task_id == task.id,
                    ArtifactRevision.user_id == task.user_id,
                )
            )
            if stored is None or digest(stored.payload) != step.output_hash:
                raise ApiError(
                    409, "workflow_checkpoint_invalid", "Stored workflow result is unavailable."
                )
            if operation == "schedule":
                await calendar_result(session, claim, stored.payload["content"]["slot_request_id"])
            return True, stored
        if payload is None:
            if step.attempts >= tasks.MAX_ATTEMPTS:
                raise ApiError(409, "step_attempts_exhausted", "Step retry budget exhausted.")
            step.state = "running"
            step.attempts += 1
            task.version += 1
            tasks.add_event(
                session, task, "step.started", {"ordinal": ordinal, "operation": operation}
            )
            return True, None
        if step.state != "running":
            raise ApiError(409, "workflow_step_not_running", "Step cannot publish.")
        if operation == "schedule":
            await calendar_result(session, claim, payload["content"]["slot_request_id"])
        request = WorkflowRequest.model_validate(claim.workflow_input["request"])
        final = ordinal == len(request.operations)
        artifact = ArtifactRevision(
            id=str(uuid4()),
            task_id=task.id,
            user_id=task.user_id,
            stream_key="result" if final else operation,
            revision=1,
            payload=payload,
            provenance={
                **provenance,
                "release": claim.release,
                "dependency_artifact_ids": [a.id for a in dependencies],
            },
            draft_envelope=claim.draft_input if operation.startswith("draft_") else None,
        )
        session.add(artifact)
        await session.flush()
        step.state, step.artifact_id, step.output_hash = "succeeded", artifact.id, digest(payload)
        task.version += 1
        tasks.add_event(
            session,
            task,
            "step.succeeded",
            {"ordinal": ordinal, "artifact_id": artifact.id, "stream_key": artifact.stream_key},
        )
        if final:
            task.state, task.error_code, task.final_artifact_id = "succeeded", None, artifact.id
            tasks.close_job(await session.get(AssistantJob, task.id))
            tasks.add_event(
                session,
                task,
                "artifact.ready",
                {"artifact_id": artifact.id, "stream_key": "result", "revision": 1},
            )
            tasks.add_event(
                session, task, "task.finished", {"state": "succeeded", "artifact_id": artifact.id}
            )
        return True, artifact


def slot_draft(claim, request, dependencies):
    calendar = next(
        a for a in dependencies if a.payload.get("kind") in {"schedule_options", "availability"}
    )
    content = calendar.payload["content"]
    if content["status"] not in {"available", "no_available_option_under_preferences"}:
        raise ApiError(409, "calendar_check_incomplete", "Recheck availability before drafting.")
    options = content["slots"]
    if options:
        lines = ["Based on my calendar, these times are available:"]
        lines += [
            f"{n}. {s['start_local']} to {s['end_local']} ({s['timezone']})"
            for n, s in enumerate(options, 1)
        ]
        lines += [
            "",
            "Please let me know which option works for you. These times are not reserved.",
        ]
    else:
        lines = [
            "I couldn't find an available time within the requested window and my scheduling "
            "preferences. Could we consider another window?"
        ]
    if request.summary_in_draft:
        summary = next(a for a in dependencies if a.payload.get("kind") == "summary")
        lines = [summary.payload["content"]["overview"], ""] + lines
    mode = "reply" if request.operations[-1] == "draft_reply" else "new"
    subject = claim.draft_input["reply"]["subject"] if mode == "reply" else "Meeting availability"
    payload = drafting.make_artifact(
        json.dumps(
            {"subject": subject, "body": "\n".join(lines), "sources": [], "unresolved_fields": []}
        ),
        claim,
        mode,
    )
    payload["assumptions"] = [
        "Draft only; review and separate email approval required.",
        "Availability covers the selected calendars only. Invitee availability is unknown. "
        "Times are not reserved.",
    ]
    payload["calendar_grounding"] = {
        "slot_request_id": content["slot_request_id"],
        "slot_ids": [s["id"] for s in options],
        "expires_at": content["expires_at"],
        "source_artifact_id": calendar.id,
        "policy": SLOT_DRAFT_POLICY,
    }
    return payload


async def run_task(factory, claim, model=None, flow_invoker=None):
    ordinal = 1
    try:
        validate_release(claim.release)
        request = WorkflowRequest.model_validate(claim.workflow_input["request"])
        base = claim.release["generation"]
        policy = summary_quality.policy_for_release(base)
        native = summary_quality.unwrap_release(base)
        manifest = (
            registry.pinned_manifest(native) if native.get("workflow") == registry.RELEASE else None
        )
        dependencies = []
        async with asyncio.timeout(TIMEOUT):
            for ordinal, operation in enumerate(request.operations, 1):
                active, artifact = await checkpoint(
                    factory, claim, ordinal, operation, dependencies
                )
                if not active:
                    return
                if artifact is None:
                    provenance = {"provider": "native", "model": None}
                    if operation == "schedule":
                        saved = claim.workflow_input["schedule"]
                        schedule = SchedulingRequest.model_validate(saved["request"])
                        body = scheduling.query_body(claim.task_id, 0, saved, schedule.constraints)
                        result = await slots.submit(claim.user_id, body)
                        async with factory.begin() as session:
                            await current(session, claim)
                            result = await calendar_result(session, claim, result.id)
                        payload = scheduling.make_artifact(saved, result)
                        if request.meeting_choice:
                            from app.schemas.calendar import parse_instant

                            selected = claim.workflow_input["choice_slot"]
                            matches = [
                                s
                                for s in payload["content"]["slots"]
                                if all(
                                    parse_instant(s[k]) == parse_instant(selected[k])
                                    for k in ("start", "end")
                                )
                            ]
                            payload["meeting_choice"] = {
                                **request.meeting_choice.model_dump(),
                                "fresh_slot_id": matches[0]["id"] if len(matches) == 1 else None,
                                "availability_confirmed": len(matches) == 1,
                                "next_step": "adopt_fresh_offer_and_select"
                                if len(matches) == 1
                                else "review_other_times",
                                "booking_approved": False,
                            }
                    elif operation in {"lookup_entity", "lookup_commitments"}:
                        from app.assistant import facts

                        async with factory.begin() as session:
                            payload = await facts.execute(
                                session,
                                claim.user_id,
                                claim.context_id,
                                operation,
                                request.entity_type,
                            )
                    elif operation.startswith("draft_"):
                        if claim.workflow_input.get("plan"):
                            async with factory.begin() as session:
                                plan = await planning.accepted_plan(
                                    session,
                                    claim.user_id,
                                    claim.workflow_input["plan"]["artifact_id"],
                                )
                                payload = planning.draft(claim, plan)
                        elif claim.workflow_input["schedule"]:
                            payload = slot_draft(claim, request, dependencies)
                        else:
                            mode = "reply" if operation == "draft_reply" else "new"
                            prompt = drafting.make_prompt(
                                claim.instruction, claim.snapshot, claim.draft_input, mode
                            )
                            entry = manifest.operations[operation] if manifest else None
                            if isinstance(entry, registry.FlowEntry):
                                generated = await (flow_invoker or FlowInvoker()).invoke(
                                    entry, prompt
                                )
                                text, provenance = generated.text, generated.provenance
                            else:
                                text, info = await (model or get_model_client()).generate(
                                    prompt, max_tokens=2500
                                )
                                provenance = {"provider": info.provider, "model": info.model}
                            payload = drafting.make_artifact(text, claim, mode)
                    else:
                        prompt = (
                            planning.make_prompt(claim)
                            if operation == "plan"
                            else summary_quality.make_prompt(
                                claim.snapshot, claim.instruction, policy=policy
                            )
                        )
                        entry = (
                            manifest.operations["summarise_thread"]
                            if manifest and operation == "summary"
                            else None
                        )
                        if isinstance(entry, registry.FlowEntry):
                            generated = await (flow_invoker or FlowInvoker()).invoke(entry, prompt)
                            text, provenance = generated.text, generated.provenance
                        else:
                            if operation == "plan":
                                text, provenance = await auxiliary.generate(
                                    "plan_actions",
                                    prompt,
                                    claim.release["auxiliary"],
                                    model=model,
                                    flow_invoker=flow_invoker,
                                )
                            else:
                                text, info = await (model or get_model_client()).generate(
                                    prompt, max_tokens=2500
                                )
                                provenance = {"provider": info.provider, "model": info.model}
                        payload = (
                            planning.make_artifact(text, claim)
                            if operation == "plan"
                            else summary_quality.make_artifact(
                                text, claim.context_id, claim.snapshot
                            )
                        )
                    active, artifact = await checkpoint(
                        factory, claim, ordinal, operation, dependencies, payload, provenance
                    )
                    if not active:
                        return
                dependencies.append(artifact)
    except (ApiError, FlowError) as exc:
        await steps.fail_step(
            factory, claim, ordinal, exc.code, isinstance(exc, FlowError) and exc.retryable
        )
    except (ProviderError, TimeoutError):
        await steps.fail_step(factory, claim, ordinal, "upstream_model_unavailable", True)
    except (ValueError, TypeError, KeyError):
        await steps.fail_step(factory, claim, ordinal, "invalid_workflow_output", False)
    except SQLAlchemyError:
        raise
    except Exception:
        await steps.fail_step(factory, claim, ordinal, "workflow_failed", False)


async def view(session, task):
    if task.workflow_input is None:
        return None
    request = WorkflowRequest.model_validate(task.workflow_input["request"])
    rows = (
        await session.scalars(
            select(AssistantStep)
            .where(AssistantStep.task_id == task.id, AssistantStep.user_id == task.user_id)
            .order_by(AssistantStep.ordinal)
        )
    ).all()
    saved = {s.ordinal: s for s in rows}
    return {
        "operations": request.operations,
        "completed_steps": sum(s.state == "succeeded" for s in rows),
        "total_steps": len(request.operations),
        "external_actions": False,
        "steps": [
            {
                "ordinal": n,
                "operation": op,
                "state": saved[n].state if n in saved else "pending",
                "artifact_id": saved[n].artifact_id if n in saved else None,
                "error_code": saved[n].error_code if n in saved else None,
            }
            for n, op in enumerate(request.operations, 1)
        ],
    }


async def check_choice(session, owner, request):
    from types import SimpleNamespace

    from app.assistant import meeting_responses

    choice = request.meeting_choice
    _, _, query, _ = await meeting_responses.source(
        session,
        owner,
        SimpleNamespace(
            context_snapshot_id=request.context_snapshot_id,
            **choice.model_dump(exclude={"slot_id"}),
        ),
    )
    from app.calendar.negotiations import chosen

    slot = chosen(query, choice.slot_id)
    if request.schedule.constraints != meeting_responses.constraints(slot):
        raise ApiError(409, "offered_time_mismatch", "Recheck precisely the selected offered time.")
    return slot


def validate_release(saved):
    expected = release_manifest()
    if any(saved.get(k) != expected[k] for k in ("workflow", "contract_hash", "scheduling")):
        raise ApiError(503, "release_unavailable", "The saved workflow release is unavailable.")
    auxiliary.validate(saved["auxiliary"])
    summary_quality.policy_for_release(saved["generation"])
    base = summary_quality.unwrap_release(saved["generation"])
    if base.get("workflow") == registry.RELEASE:
        registry.pinned_manifest(base)
    elif base != registry.routing.release_manifest():
        raise ApiError(503, "release_unavailable", "Saved native model settings changed.")
