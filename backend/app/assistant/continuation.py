"""Backend-owned questions and atomic typed continuation; answers never become commands."""

import re
from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant import drafting, ui_routing
from app.assistant.source_data import context_data
from app.assistant.summary import digest
from app.db.models import AssistantJob, ContextSnapshot, TaskInput, TaskQuestion, Thread, User
from app.planner.intent_router import require_context
from app.schemas.assistant import DraftOptions, RouteDecision
from app.schemas.continuation import TaskInputRequest

VERSION = "typed-continuation-1.0.0"
MAX_INPUTS = 5
QUESTION_HOURS = 24
FIELDS = {
    "source_context": "context_snapshot_id",
    "reference_mapping": "context_snapshot_id",
    "message_selection": "context_snapshot_id",
    "message_text": "context_snapshot_id",
    "reply_target": "reply_message_id",
    "recipient": "recipients",
    "timezone": "timezone",
    "duration_minutes": "duration_minutes",
    "date_range": "date_phrase",
    "time": "time_phrase",
    "am_or_pm": "am_or_pm",
}


def release_manifest() -> dict:
    return {
        "version": VERSION,
        "contract_hash": digest(
            {
                "schema": TaskInputRequest.model_json_schema(),
                "fields": FIELDS,
                "max_inputs": MAX_INPUTS,
                "question_hours": QUESTION_HOURS,
                "ui_binding_hash": ui_routing.contract_hash(),
                "policy": (
                    "typed-overlays:original-goal:owned-fresh-context:context-time-v1:no-actions-v1"
                ),
            }
        ),
    }


def validate_release(release):
    if release != release_manifest():
        raise ApiError(503, "release_unavailable", "The saved continuation release is unavailable.")


async def current_input(session, task) -> TaskInput | None:
    if task.input_version == 0:
        return None
    value = await session.scalar(
        select(TaskInput).where(
            TaskInput.task_id == task.id,
            TaskInput.user_id == task.user_id,
            TaskInput.input_version == task.input_version,
        )
    )
    if value is None or value.context_snapshot_id != task.effective_context_snapshot_id:
        raise ApiError(409, "input_unavailable", "The saved input or its source was removed.")
    return value


async def question_view(session, task) -> dict | None:
    if task.state != "needs_clarification" or task.continuation_release is None:
        return None
    question = await session.scalar(
        select(TaskQuestion).where(
            TaskQuestion.task_id == task.id,
            TaskQuestion.user_id == task.user_id,
            TaskQuestion.input_version == task.input_version,
            TaskQuestion.state == "open",
        )
    )
    if question is None:
        return None
    now = await session.scalar(select(func.clock_timestamp()))
    return {
        "question_id": question.id,
        "schema_version": "1.0",
        "expected_version": question.task_version,
        "input_version": question.input_version,
        "expires_at": question.expires_at.isoformat(),
        "expired": question.expires_at <= now,
        **question.payload,
    }


def open_question(session, task, now, *, context_id=None, source_hash=None) -> str | None:
    """Called under the task lease fence, in the same transaction as the stopped state."""
    if task.scheduling_input is not None:
        from app.assistant import scheduling

        return scheduling.open_question(session, task, now)
    if task.continuation_release is None:
        return None  # Historical jobs keep their prior terminal behavior.
    validate_release(task.continuation_release)
    if task.input_version >= MAX_INPUTS:
        return "clarification_limit_reached"
    missing = task.route["decision"]["missing_fields"]
    # Never ask for an approval target or invent a form for an unsupported field.
    if not missing or any(field not in FIELDS for field in missing):
        return "clarification_fields_unavailable"
    fields = list(dict.fromkeys(FIELDS[field] for field in missing))
    if "reply_target" in missing and context_id is None:
        fields.append("context_snapshot_id")
    session.add(
        TaskQuestion(
            id=str(uuid4()),
            task_id=task.id,
            user_id=task.user_id,
            task_version=task.version,
            input_version=task.input_version,
            state="open",
            payload={
                "kind": "missing_inputs",
                "fields": fields,
                "missing_fields": missing,
                "prompt": "Provide or select: " + ", ".join(fields) + ".",
                "context_snapshot_id": context_id,
                "source_hash": source_hash,
                "route_hash": digest(task.route),
            },
            expires_at=now + timedelta(hours=QUESTION_HOURS),
        )
    )
    return None


async def cancel_question(session, task):
    question = await session.scalar(
        select(TaskQuestion).where(
            TaskQuestion.task_id == task.id,
            TaskQuestion.user_id == task.user_id,
            TaskQuestion.input_version == task.input_version,
            TaskQuestion.state == "open",
        )
    )
    if question:
        question.state = "cancelled"


async def fresh_context(session, user_id, context_id, previous_id):
    if context_id is None:
        return None
    context = await session.scalar(
        select(ContextSnapshot).where(
            ContextSnapshot.id == context_id,
            ContextSnapshot.user_id == user_id,
        )
    )
    if context is None:
        raise ApiError(404, "context_not_found", "Select an accessible saved snapshot.")
    if previous_id and context_id != previous_id:
        previous = await session.scalar(
            select(ContextSnapshot).where(
                ContextSnapshot.id == previous_id,
                ContextSnapshot.user_id == user_id,
            )
        )
        if previous is None or previous.thread_id != context.thread_id:
            raise ApiError(
                409, "source_scope_changed", "Continue with a fresh capture of the same thread."
            )
    if context_data(context).get("source_mode") == "gmail_on_demand":
        from app.assistant.source_data import validate

        await validate(session, user_id, context_data(context))
    # Hold the source version stable until the answer transaction commits.
    thread = await session.scalar(
        select(Thread)
        .where(
            Thread.id == context.thread_id,
            Thread.user_id == user_id,
        )
        .with_for_update(read=True)
    )
    if thread is None or thread.version != context_data(context).get("thread_version"):
        raise ApiError(409, "source_changed", "Sync and capture the source again before answering.")
    if not any(m["body"].strip() for m in context_data(context)["messages"]):
        raise ApiError(409, "context_empty", "Select a source with synced text.")
    return context


def resolve_time_context(route, instruction, fields, snapshot, envelope):
    """Use explicit, unambiguous time evidence; never infer PM from business hours.

    Higher-priority user input wins. Thread evidence requires one selected message
    (or a single-message snapshot), an exact matching clock time and no conflicting
    times. Quoted history and negated/alternative statements are not resolved here.
    """
    value = RouteDecision.model_validate(route["decision"]).model_dump()
    if value["status"] == "unsupported" or not set(value["operations"]) & {"check_time"}:
        return route
    phrase = value["parameters"]["time_phrase"] or ""
    bare = re.fullmatch(r"\s*(?:at\s+)?([1-9]|1[0-2])(?::([0-5][0-9]))?\s*", phrase, re.I)
    if not bare:
        return route
    hour, minute = int(bare[1]), int(bare[2] or 0)

    def evidence(text, *, dayparts):
        # These constructs need a semantic resolver; do not guess using keywords.
        if re.search(
            r"\b(?:not|no|never|instead|rather|or|but|changed|cancelled)\b|n't", text, re.I
        ):
            return set(), True
        times = list(
            re.finditer(r"(?<![\d:])([1-9]|1[0-2])(?::([0-5][0-9]))?\s*(AM|PM)\b", text, re.I)
        )
        if any((int(m[1]), int(m[2] or 0)) != (hour, minute) for m in times):
            return set(), True
        values = {m[3].upper() for m in times}
        if dayparts:
            parts = re.findall(r"\b(morning|afternoon|evening)\b", text, re.I)
            values.update("AM" if part.lower() == "morning" else "PM" for part in parts)
        return values, len(values) > 1

    # Explicit typed AM/PM is already applied by resolve_route.
    user_text = (
        instruction + " " + (fields.get("date_phrase") or value["parameters"]["date_phrase"] or "")
    )
    values, blocked = evidence(user_text, dayparts=True)
    source = "user request/date context"
    if not values and not blocked and snapshot:
        messages = snapshot.get("messages", [])
        target = (envelope or {}).get("reply_message_id")
        if target:
            messages = [m for m in messages if m.get("message_id") == target]
        if len(messages) == 1:
            body = messages[0].get("body", "")
            if not re.search(r"(?im)^>|forwarded message|wrote:", body):
                values, blocked = evidence(body, dayparts=False)
                source = "selected source message " + str(messages[0].get("message_id", ""))
    if blocked or len(values) != 1:
        return route
    suffix = next(iter(values))
    # 12 in the morning/evening needs an explicit clock suffix, not a daypart guess.
    if (
        hour == 12
        and not re.search(r"12(?::[0-5][0-9])?\s*(?:AM|PM)\b", user_text, re.I)
        and source.startswith("user")
    ):
        return route
    value["parameters"]["time_phrase"] = phrase.strip() + " " + suffix
    value["missing_fields"] = [f for f in value["missing_fields"] if f != "am_or_pm"]
    if not value["missing_fields"] and value["status"] == "needs_clarification":
        value.update(status="ready", clarification=None)
    elif value["missing_fields"]:
        value["clarification"] = "Provide or select: " + ", ".join(value["missing_fields"]) + "."
    value["rationale"] = "Time suffix resolved from " + source + "."
    return {**route, "decision": RouteDecision.model_validate(value).model_dump()}


def resolve_route(route, instruction, fields, context, envelope):
    """Resolve the saved goal deterministically. Never classify the answer as a new request."""
    payload = context_data(context) if context else None
    context_id = context.id if context else None
    binding = ui_routing.bind_reference(instruction, payload)
    if binding is not None and "context_snapshot_id" in fields:
        return ui_routing.reference_route(binding, context_id)
    value = RouteDecision.model_validate(route["decision"]).model_dump()
    value["context_snapshot_id"] = context_id
    parameters = value["parameters"]
    for field in ("date_phrase", "time_phrase", "duration_minutes"):
        if field in fields:
            parameters[field] = fields[field]
    if fields.get("am_or_pm") and parameters["time_phrase"]:
        # Append once to the original phrase, not a previously resolved phrase.
        phrase = parameters["time_phrase"]
        if phrase.upper().endswith(("AM", "PM")):
            if not phrase.upper().endswith(fields["am_or_pm"]):
                raise ApiError(422, "conflicting_time", "The time and AM/PM answer disagree.")
        else:
            parameters["time_phrase"] = phrase + " " + fields["am_or_pm"]
    resolved = {missing for missing, field in FIELDS.items() if field in fields}
    if context:
        resolved.add("source_context")
    if envelope and envelope.get("reply"):
        resolved.add("reply_target")
    if envelope and envelope.get("to"):
        resolved.add("recipient")
    missing = [field for field in value["missing_fields"] if field not in resolved]
    value.update(
        status="needs_clarification" if missing else "ready",
        missing_fields=missing,
        clarification="Provide or select: " + ", ".join(missing) + "." if missing else None,
    )
    value = resolve_time_context(
        {**route, "decision": value}, instruction, fields, payload, envelope
    )["decision"]
    checked = require_context(
        RouteDecision.model_validate(value),
        has_source=context is not None,
        has_reply_target=bool(envelope and envelope.get("reply")),
        has_recipients=bool(envelope and envelope.get("to")),
    )
    # Ask only for prerequisites still unresolved after applying available context.
    # Timezone remains a typed backend input outside the historical route schema.
    missing = [
        field
        for field in checked.missing_fields
        if not (field == "timezone" and fields.get("timezone"))
    ]
    value = checked.model_dump()
    value.update(
        status="needs_clarification" if missing else "ready",
        missing_fields=missing,
        clarification="Provide or select: " + ", ".join(missing) + "." if missing else None,
    )
    if not missing and value["output_kind"] == "clarification":
        final_operation = value["operations"][-1] if value["operations"] else None
        value["output_kind"] = {
            "summarise_thread": "summary",
            "draft_reply": "draft",
            "draft_new": "draft",
            "plan_actions": "plan",
            "check_time": "schedule_options",
            "suggest_slots": "schedule_options",
        }.get(final_operation, "answer")
    return {**route, "decision": RouteDecision.model_validate(value).model_dump()}


async def accept_input(session, user_id: int, task_id: str, request: TaskInputRequest):
    from app.assistant import tasks

    task = await tasks.owned_task(session, user_id, task_id, lock=True)
    if task.scheduling_input is not None:
        raise ApiError(
            409, "scheduling_input_required", "Use this task's scheduling input endpoint."
        )
    request_hash = digest(request.model_dump())
    existing = await session.scalar(
        select(TaskInput).where(
            TaskInput.task_id == task.id,
            TaskInput.user_id == user_id,
            TaskInput.request_id == request.request_id,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise ApiError(
                409, "idempotency_conflict", "Input request ID was used for another answer."
            )
        return task  # Replay returns current state; it never queues work again.
    if task.continuation_release is None:
        raise ApiError(
            409, "continuation_unavailable", "Submit a new request for this historical task."
        )
    validate_release(task.continuation_release)
    question = await session.scalar(
        select(TaskQuestion).where(
            TaskQuestion.id == request.question_id,
            TaskQuestion.task_id == task.id,
            TaskQuestion.user_id == user_id,
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
        raise ApiError(
            409, "question_changed", "The task or question changed; reload before answering."
        )
    now = await session.scalar(select(func.clock_timestamp()))
    if question.expires_at <= now:
        raise ApiError(409, "question_expired", "This question expired; submit a new request.")
    answer = request.answer.model_dump(exclude_none=True)
    allowed = set(question.payload["fields"])
    if set(answer) - (allowed | {"context_snapshot_id"}) or not set(answer) & allowed:
        raise ApiError(
            422, "answer_field_not_requested", "Answer only the fields in this question."
        )
    previous = await current_input(session, task)
    fields = {**(previous.effective_fields if previous else {}), **answer}
    previous_context_id = previous.context_snapshot_id if previous else task.context_snapshot_id
    context_id = answer.get("context_snapshot_id", previous_context_id)
    context = await fresh_context(session, user_id, context_id, previous_context_id)
    if set(question.payload["missing_fields"]) & {
        "reference_mapping",
        "message_selection",
        "message_text",
    }:
        binding = ui_routing.bind_reference(
            task.instruction, context_data(context) if context else None
        )
        if not binding or binding["status"] != "ready":
            raise ApiError(
                422, "reference_not_resolved", "Capture a view that resolves the requested message."
            )
    envelope = previous.draft_input if previous else task.draft_input
    options = (
        {key: envelope[key] for key in ("to", "cc", "bcc", "reply_message_id")} if envelope else {}
    )
    if "recipients" in answer:
        options["to"] = answer["recipients"]
    if "reply_message_id" in answer:
        options["reply_message_id"] = answer["reply_message_id"]
    if options:
        user = await session.get(User, user_id)
        try:
            bound_options = DraftOptions.model_validate(options)
        except ValidationError:
            raise ApiError(
                422, "invalid_recipients", "Recipients must be valid and distinct."
            ) from None
        envelope = await drafting.bind_input(session, user, bound_options, context)
    route = resolve_route(task.route, task.instruction, fields, context, envelope)
    # Answers are a separate immutable input stream; original request/bindings/releases stay intact.
    task.input_version += 1
    task.effective_context_snapshot_id = context_id
    session.add(
        TaskInput(
            id=str(uuid4()),
            task_id=task.id,
            user_id=user_id,
            question_id=question.id,
            request_id=request.request_id,
            request_hash=request_hash,
            input_version=task.input_version,
            answer=answer,
            effective_fields=fields,
            context_snapshot_id=context_id,
            source_hash=context.source_hash if context else None,
            draft_input=envelope,
        )
    )
    question.state = "answered"
    task.route = route
    task.version += 1
    task.state, task.error_code = "queued", None
    job = await session.get(AssistantJob, task.id)
    job.state, job.attempts, job.available_at = "queued", 0, now
    job.lease_token, job.lease_expires_at = None, None
    tasks.add_event(
        session,
        task,
        "task.input_accepted",
        {
            "input_version": task.input_version,
            "state": "queued",
        },
    )
    await session.flush()
    await session.refresh(task)
    return task
