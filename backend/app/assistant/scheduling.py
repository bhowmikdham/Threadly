"""Durable, explicitly selected scheduling reads with typed clarification.

No language-model output enters this handler. Account/preferences/source fences precede
task locks; the Calendar service runs outside transactions and owns read idempotency.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import ApiError
from app.assistant import continuation, steps, tasks
from app.assistant.summary import digest
from app.calendar import service, slots
from app.db.models import CalendarPreference, CalendarSlotRequest, TaskInput, TaskQuestion
from app.schemas.calendar import Preferences
from app.schemas.scheduling import (
    SchedulingConstraints,
    SchedulingInputRequest,
    SchedulingRequest,
    validate_operation,
)
from app.schemas.slots import SLOT_POLICY, SlotRequest

RELEASE = "assistant-scheduling-1.0.0"
TIMEOUT_SECONDS = 120
QUESTIONS = {
    "date": "Which date should I check? Use today, tomorrow or YYYY-MM-DD.",
    "at_time": "Which clock time should I check?",
    "meridiem": "Does that time mean AM or PM?",
    "fold": "That local clock time occurs twice. Choose the first or second occurrence.",
}


def release_manifest():
    return {
        "workflow": RELEASE,
        "contract_hash": digest(
            {
                "request": SchedulingRequest.model_json_schema(),
                "answer": SchedulingInputRequest.model_json_schema(),
                "slot_policy": SLOT_POLICY,
                "questions": QUESTIONS,
                "policy": "typed-only-saved-anchor-source-fence-current-result-v1",
                "timeout": TIMEOUT_SECONDS,
                "max_answer_rounds": continuation.MAX_INPUTS,
                "question_hours": continuation.QUESTION_HOURS,
            }
        ),
    }


def continuation_manifest():
    return {"version": "scheduling-continuation-1.0.0", **release_manifest()}


def validate_release(release, continuation_release):
    if release != release_manifest() or continuation_release != continuation_manifest():
        raise ApiError(503, "release_unavailable", "The saved scheduling release is unavailable.")


async def bind_input(session, owner, request, context):
    user = await service.account(session, owner, lock=True)
    pref = await session.get(CalendarPreference, owner, with_for_update=True)
    service.check_pref(pref, request.expected_preferences_version, user.google_account_version)
    if context:
        await continuation.fresh_context(session, owner, context.id, context.id)
    now = await session.scalar(select(func.clock_timestamp()))
    anchor = now
    if request.anchor_message_id:
        messages = [
            m for m in context.payload["messages"] if m["message_id"] == request.anchor_message_id
        ]
        if len(messages) != 1 or not messages[0].get("sent_at"):
            raise ApiError(
                422, "scheduling_anchor_missing", "Select a captured message with a date."
            )
        anchor = datetime.fromisoformat(messages[0]["sent_at"])
        if anchor.tzinfo is None:
            raise ApiError(
                422, "scheduling_anchor_missing", "The message needs a timezone-aware date."
            )
    return {
        "request": request.model_dump(),
        "account_version": user.google_account_version,
        "preferences": pref.preferences,
        "anchor_at": anchor.isoformat(),
        "anchor_source": "source_message" if request.anchor_message_id else "request_received",
        "source_hash": digest(context.payload) if context else None,
    }


async def check_current(session, owner, saved, context_id):
    user = await service.account(session, owner, lock=True, expected=saved["account_version"])
    pref = await session.get(CalendarPreference, owner, with_for_update=True)
    service.check_pref(
        pref, saved["request"]["expected_preferences_version"], user.google_account_version
    )
    if pref.preferences != saved["preferences"]:
        raise service.conflict()
    context = await continuation.fresh_context(session, owner, context_id, context_id)
    if (digest(context.payload) if context else None) != saved["source_hash"]:
        raise ApiError(409, "source_changed", "Capture the current thread and start a new task.")


def effective_constraints(saved, answers):
    result = SchedulingConstraints.model_validate(
        {
            **saved["request"]["constraints"],
            **(answers or {}),
        }
    )
    validate_operation(saved["request"]["operation"], result)
    return result


def query_body(task_id, input_version, saved, constraints):
    values = constraints.model_dump()
    if values["date"] in {"today", "tomorrow"}:
        zone = values["timezone"] or saved["preferences"]["timezone"]
        anchor = datetime.fromisoformat(saved["anchor_at"]).astimezone(ZoneInfo(zone))
        values["date"] = (anchor.date() + timedelta(days=values["date"] == "tomorrow")).isoformat()
    return SlotRequest(
        request_id=f"assistant-schedule-{task_id}-{input_version}",
        expected_preferences_version=saved["request"]["expected_preferences_version"],
        **values,
    )


def missing_question(saved, constraints):
    fields = []
    if constraints.date is None:
        fields.append("date")
    if saved["request"]["operation"] == "check_time" and constraints.at_time is None:
        fields.append("at_time")
    return {"fields": fields, "reason": "missing_constraints", "choices": []} if fields else None


def resolution_question(resolution):
    field = resolution["question"]["field"]
    if field not in QUESTIONS:
        raise ApiError(409, "scheduling_question_unavailable", "Start a new scheduling request.")
    return {
        "fields": [field],
        "reason": resolution["reason"],
        "choices": resolution["question"]["choices"],
    }


def route(saved, question=None):
    request = saved["request"]
    return {
        "router_version": RELEASE,
        "source": "explicit",
        "decision": {
            "schema_version": "1.0",
            "intent": "plan_schedule",
            "status": "needs_clarification" if question else "ready",
            "operations": [request["operation"]],
            "output_kind": "schedule_options",
            "context_snapshot_id": request["context_snapshot_id"],
            "parameters": {
                "date_phrase": None,
                "time_phrase": None,
                "duration_minutes": None,
                "slot_count": None,
                "tone": None,
                "recipient_refs": [],
            },
            "missing_fields": question["fields"] if question else [],
            "clarification": " ".join(QUESTIONS[f] for f in question["fields"])
            if question
            else None,
            "rationale": "Explicit scheduling request; Calendar facts are backend-owned.",
            "requested_action": "none",
        },
        "scheduling_question": question,
    }


def open_question(session, task, now):
    validate_release(task.release, task.continuation_release)
    if task.input_version >= continuation.MAX_INPUTS:
        return "clarification_limit_reached"
    question = task.route["scheduling_question"]
    session.add(
        TaskQuestion(
            id=str(uuid4()),
            task_id=task.id,
            user_id=task.user_id,
            task_version=task.version,
            input_version=task.input_version,
            state="open",
            payload={
                "kind": "scheduling_constraints",
                **question,
                "prompt": task.route["decision"]["clarification"],
                "route_hash": digest(task.route),
                "input_url": f"/assistant/tasks/{task.id}/scheduling-inputs",
                "anchor_at": task.scheduling_input["anchor_at"],
                "anchor_source": task.scheduling_input["anchor_source"],
            },
            expires_at=now + timedelta(hours=continuation.QUESTION_HOURS),
        )
    )
    return None


async def accept_input(session, owner, task_id, request):
    # Snapshot first, then acquire locks in the same account -> source -> task order as publication.
    task = await tasks.owned_task(session, owner, task_id)
    if task.scheduling_input is None:
        raise ApiError(409, "not_scheduling_task", "Use the question's indicated input endpoint.")
    saved, context_id = task.scheduling_input, task.context_snapshot_id
    # Replay remains readable after revocation, without another job or Calendar call.
    previous_receipt = await session.scalar(
        select(TaskInput).where(
            TaskInput.task_id == task_id,
            TaskInput.user_id == owner,
            TaskInput.request_id == request.request_id,
        )
    )
    hashed = digest(request.model_dump())
    if previous_receipt:
        if previous_receipt.request_hash != hashed:
            raise ApiError(409, "idempotency_conflict", "Answer key was used for other input.")
        return task
    await check_current(session, owner, saved, context_id)
    task = await tasks.owned_task(session, owner, task_id, lock=True)
    await session.refresh(task)  # Another answer/cancel may have committed during the lock wait.
    validate_release(task.release, task.continuation_release)
    receipt = await session.scalar(
        select(TaskInput).where(
            TaskInput.task_id == task_id,
            TaskInput.user_id == owner,
            TaskInput.request_id == request.request_id,
        )
    )
    if receipt:
        if receipt.request_hash != hashed:
            raise ApiError(409, "idempotency_conflict", "Answer key was used for other input.")
        return task
    question = await session.scalar(
        select(TaskQuestion).where(
            TaskQuestion.id == request.question_id,
            TaskQuestion.task_id == task_id,
            TaskQuestion.user_id == owner,
        )
    )
    if question is None:
        raise ApiError(404, "question_not_found", "Unknown question for this task.")
    if (
        task.state != "needs_clarification"
        or question.state != "open"
        or question.task_version != task.version
        or task.version != request.expected_version
        or question.input_version != task.input_version
        or question.payload["route_hash"] != digest(task.route)
    ):
        raise ApiError(409, "question_changed", "The task or question changed; reload it.")
    now = await session.scalar(select(func.clock_timestamp()))
    if question.expires_at <= now:
        raise ApiError(409, "question_expired", "This question expired; start a new task.")
    answer = request.answer.model_dump(exclude_none=True)
    if set(answer) - set(question.payload["fields"]):
        raise ApiError(422, "answer_field_not_requested", "Answer only the requested fields.")
    previous = await continuation.current_input(session, task)
    fields = {**(previous.effective_fields if previous else {}), **answer}
    # A new date/time must not inherit a fold choice for a different wall instant.
    if set(answer) & {"date", "at_time"}:
        fields["fold"] = None
    if "at_time" in answer:
        fields["meridiem"] = None
    try:
        effective_constraints(saved, fields)
    except ValueError:
        raise ApiError(
            422, "invalid_scheduling_answer", "Use a valid date or clock value."
        ) from None
    task.input_version += 1
    task.effective_context_snapshot_id = context_id
    session.add(
        TaskInput(
            id=str(uuid4()),
            task_id=task.id,
            user_id=owner,
            question_id=question.id,
            request_id=request.request_id,
            request_hash=hashed,
            input_version=task.input_version,
            answer=answer,
            effective_fields=fields,
            context_snapshot_id=context_id,
            source_hash=saved["source_hash"],
            draft_input=None,
        )
    )
    question.state = "answered"
    task.route = None
    task.version += 1
    task.state, task.error_code = "queued", None
    from app.db.models import AssistantJob

    job = await session.get(AssistantJob, task.id)
    job.state, job.attempts, job.available_at = "queued", 0, now
    job.lease_token, job.lease_expires_at = None, None
    tasks.add_event(
        session,
        task,
        "task.input_accepted",
        {"input_version": task.input_version, "state": "queued"},
    )
    await session.flush()
    await session.refresh(task)
    return task


def make_artifact(saved, result):
    exact = saved["request"]["operation"] == "check_time"
    status = (
        "unknown"
        if result.state == "unknown"
        else "elapsed"
        if result.reason == "window_elapsed"
        else "available"
        if result.slots
        else "no_available_option_under_preferences"
    )
    texts = {
        "unknown": "Calendar coverage is incomplete; availability is unknown.",
        "elapsed": "The requested time window has elapsed.",
        "available": (
            f"Found {len(result.slots)} available meeting option(s) in your selected calendars."
        ),
        "no_available_option_under_preferences": (
            "No available option fits this request and your saved preferences."
        ),
    }
    return {
        "schema_version": "1.0",
        "kind": "availability" if exact else "schedule_options",
        "content": {
            "status": status,
            "text": texts[status],
            "slot_request_id": result.id,
            "slots": [s.model_dump(mode="json") for s in result.slots],
            "resolution": result.resolution,
            "reason": result.reason,
            "calculated_at": result.calculated_at.isoformat() if result.calculated_at else None,
            "expires_at": result.expires_at.isoformat(),
            "anchor_at": saved["anchor_at"],
            "anchor_source": saved["anchor_source"],
            "anchor_message_id": saved["request"]["anchor_message_id"],
            "availability_scope": "user_selected_calendars",
            "attendee_availability": "unknown",
            "reservation": False,
            "booking_approved": False,
            "event_created": False,
        },
    }


async def run_task(factory, claim):
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            validate_release(claim.release, claim.continuation_release)
            saved = claim.scheduling_input
            if saved is None:
                raise ApiError(
                    409, "scheduling_input_missing", "Saved scheduling input is missing."
                )
            constraints = effective_constraints(saved, claim.resolved_inputs)
            async with factory.begin() as session:
                await check_current(session, claim.user_id, saved, claim.context_id)
                if await steps.fenced_task(session, claim) is None:
                    return
                question = missing_question(saved, constraints)
                if not question:
                    body = query_body(claim.task_id, claim.input_version, saved, constraints)
                    now = await session.scalar(select(func.clock_timestamp()))
                    resolution = slots.prepare(
                        body,
                        Preferences.model_validate(saved["preferences"]),
                        datetime.fromisoformat(saved["anchor_at"]),
                        now,
                    )
                    if resolution["state"] == "needs_clarification":
                        question = resolution_question(resolution)
                if not await tasks.save_route(session, claim, route(saved, question)):
                    return
                if question:
                    await tasks.finish(session, claim, stopped_state="needs_clarification")
                    return
            result = await slots.submit(claim.user_id, body)
            if result.state not in {"complete", "unknown"}:
                raise ApiError(409, "calendar_check_incomplete", "Start a fresh scheduling task.")
            async with factory.begin() as session:
                await check_current(session, claim.user_id, saved, claim.context_id)
                row = await session.scalar(
                    select(CalendarSlotRequest).where(
                        CalendarSlotRequest.id == result.id,
                        CalendarSlotRequest.user_id == claim.user_id,
                    )
                )
                if row is None or row.request != body.model_dump(mode="json"):
                    raise ApiError(
                        409, "calendar_query_mismatch", "Calendar result does not match this task."
                    )
                await slots.check_current(session, claim.user_id, row)
                # The database receipt, not a transport-returned payload, supplies all facts.
                result = slots.view(row)
                if result.state not in {"complete", "unknown"}:
                    raise ApiError(
                        409, "calendar_check_incomplete", "Start a fresh scheduling task."
                    )
                await tasks.finish(
                    session,
                    claim,
                    payload=make_artifact(saved, result),
                    provenance={
                        "provider": "native",
                        "model": None,
                        "release": claim.release,
                        "input_version": claim.input_version,
                        "slot_request_id": result.id,
                        "context_snapshot_id": claim.context_id,
                        "source_hash": saved["source_hash"],
                    },
                )
    except ApiError as exc:
        error = exc.code
    except TimeoutError:
        error = "calendar_check_incomplete"
    except (ValueError, TypeError, KeyError, OverflowError):
        error = "invalid_scheduling_input"
    except SQLAlchemyError:
        raise  # Lease recovery reuses the same read key; failed publication must roll back.
    except Exception:
        error = "scheduling_failed"
    else:
        return
    async with factory.begin() as session:
        await tasks.finish(session, claim, error_code=error)


async def artifact_usability(session, task, artifact):
    try:
        validate_release(task.release, task.continuation_release)
        await check_current(session, task.user_id, task.scheduling_input, task.context_snapshot_id)
        row = await session.scalar(
            select(CalendarSlotRequest).where(
                CalendarSlotRequest.id == artifact.payload["content"]["slot_request_id"],
                CalendarSlotRequest.user_id == task.user_id,
            )
        )
        if row is None:
            raise ApiError(409, "calendar_slot_request_missing", "Calendar result was removed.")
        await slots.check_current(session, task.user_id, row)
        if artifact.payload != make_artifact(task.scheduling_input, slots.view(row)):
            raise ApiError(409, "calendar_query_mismatch", "Saved result does not match the query.")
        return {"usable": True, "blockers": [], "has_available_options": bool(row.result["slots"])}
    except ApiError as exc:
        return {"usable": False, "blockers": [exc.code], "has_available_options": False}
