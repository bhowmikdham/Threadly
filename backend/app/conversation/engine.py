"""Finite read/decision loop, independent of transport and persistence."""

import asyncio
import json
import logging
import random
import re
from datetime import UTC, datetime

from pydantic import ValidationError

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.calendar import conversation_guard, event_draft
from app.calendar import intent as calendar_intent
from app.conversation import budget, email_draft, email_repair, email_review, mail_goal
from app.conversation.prompt import PROMPT, RELEASE
from app.model_client.conversation import ConversationModel, ConversationProviderError
from app.model_client.providers import ProviderError
from app.schemas.calendar_tools import CALENDAR_READ_TOOLS
from app.schemas.conversation import EMAIL_DRAFT_TOOLS, TOOLS, PrepareWorkflow, Respond, tool_config

TERMINAL = (
    set(CALENDAR_READ_TOOLS)
    | EMAIL_DRAFT_TOOLS
    | {
        "respond",
        "prepare_calendar_event",
        "prepare_workflow",
        "prepare_email_draft",
        "review_email_draft",
        "review_conversation_goal",
        "answer_question",
        "revise_draft",
        "read_calendar",
        "check_day_availability",
        "retry_calendar_read",
    }
)
MAX_CALLS = 8
RETRYABLE_PROVIDER_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ReadTimeoutError",
        "ConnectTimeoutError",
        "EndpointConnectionError",
        "ConnectionClosedError",
    }
)
log = logging.getLogger("threadly.conversation.provider")


class IncompleteSearchCoverage(ValueError):
    """A response turns a bounded search result into an unqualified global claim."""


class MissingSourceEvidence(ValueError):
    """A source-based answer omitted citations, including an unread ranking claim."""


class FreshSearchRequired(ValueError):
    """The latest explicit mailbox request cannot answer from a previous search."""


class UnverifiedSourceEvidence(ValueError):
    """A response cited a reference or quote outside the read observations."""


class UninspectedNewerResults(ValueError):
    """A latest-result answer skipped newer returned search candidates."""

    def __init__(self, references):
        self.references = references
        super().__init__("Newer search results remain unread")


class UnsupportedClarificationClaim(ValueError):
    """An uncited clarification asserts an inbox ranking instead of only asking."""


class DraftWorkflowRequired(ValueError):
    """A complete user-authored compose request needs a reviewable draft task."""


class IncorrectCapabilityAdvice(ValueError):
    """Reconnecting cannot enable an account's server-disabled send capability."""


async def run(context, runtime, model=None):
    mail_goal.begin_turn(runtime)
    if email_review.requested(runtime):
        return {
            **await email_review.review(runtime),
            "release": RELEASE,
            "trace": [{"tool": "review_email_draft", "status": "ok"}],
        }
    messages = [{"role": "user", "content": [{"text": json.dumps(budget.fit(context))}]}]
    seen, calls, trace = set(), 0, []
    email_errors = {}
    last_verified_evidence = []
    try:
        async with asyncio.timeout(120) as turn_timeout:
            while calls < MAX_CALLS:
                for attempt in range(3):
                    try:
                        message = await (model or ConversationModel()).decide(
                            PROMPT, messages, tool_config()
                        )
                        break
                    except ConversationProviderError as exc:
                        # Log only normalized diagnostics. SDK exception text can
                        # contain URLs and provider response bodies; never include it.
                        log.warning(
                            "conversation_bedrock_attempt_failed code=%s http_status=%s "
                            "request_id=%s elapsed_ms=%s attempt=%s",
                            exc.code,
                            exc.http_status,
                            exc.request_id,
                            exc.elapsed_ms,
                            attempt + 1,
                        )
                        if exc.code not in RETRYABLE_PROVIDER_CODES or attempt == 2:
                            raise
                        delay = 2 ** (attempt + 1) + random.uniform(0, 0.5)
                        remaining = turn_timeout.when() - asyncio.get_running_loop().time()
                        if remaining <= delay + 5:
                            raise
                        await asyncio.sleep(delay)
                blocks = message.get("content", [])
                requests = [b["toolUse"] for b in blocks if "toolUse" in b]
                if not requests or len(requests) > MAX_CALLS - calls:
                    break
                messages.append(message)
                if len(requests) > 1 and all(
                    call.get("name") == "prepare_workflow" for call in requests
                ):
                    # Some providers split one compound user request into several
                    # terminal workflow calls. Execute none of those partial plans.
                    # Merge only model-selected references; Runtime still binds the
                    # durable instruction to user-authored turns and authorizes every
                    # requested operation before reserving a proposal.
                    calls += len(requests)
                    try:
                        if conversation_guard.requires_preparation(runtime):
                            raise conversation_guard.CalendarPreparationRequired
                        arguments = _merge_prepare_workflows(requests)
                        key = digest({"tool": "prepare_workflow", "input": arguments.model_dump()})
                        if key in seen:
                            raise ValueError("Repeated compound workflow call")
                        seen.add(key)
                        outcome = await runtime.call("prepare_workflow", arguments)
                        trace.append({"tool": "prepare_workflow", "status": "ok"})
                        return {**outcome, "release": RELEASE, "trace": trace}
                    except conversation_guard.CalendarPreparationRequired:
                        trace.append(
                            {
                                "tool": "prepare_workflow",
                                "status": "invalid",
                                "reason": "calendar_preparation_required",
                            }
                        )
                        results = [
                            _tool_error(
                                call["toolUseId"],
                                "calendar_preparation_required",
                                "Use prepare_calendar_event for this Calendar request. "
                                "Preserve the supplied title, date and time.",
                            )
                            for call in requests
                        ]
                    except (ValidationError, ValueError):
                        trace.append({"tool": "prepare_workflow", "status": "invalid"})
                        message = (
                            "Combine every requested operation into one prepare_workflow "
                            "call with compound=true. Use intent=plan_schedule when "
                            "scheduling is included, and bind the plan to one source "
                            "reference with non-conflicting recipient roles."
                        )
                        results = [
                            _tool_error(call["toolUseId"], "invalid_tool_input", message)
                            for call in requests
                        ]
                    except ApiError as exc:
                        trace.append({"tool": "prepare_workflow", "status": exc.code})
                        results = [
                            _tool_error(call["toolUseId"], exc.code, exc.message)
                            for call in requests
                        ]
                    messages.append({"role": "user", "content": results})
                    if len(json.dumps(messages)) > 85000:
                        break
                    continue
                results = []
                for call in requests:
                    calls += 1
                    name, values = call["name"], call["input"]
                    try:
                        if name not in TOOLS:
                            raise ValueError("Unknown tool")
                        if name == "prepare_calendar_event":
                            runtime.calendar_event_attempted = True
                            runtime.calendar_event_new_goal = not values.get(
                                "continue_previous", False
                            )
                            runtime.calendar_event_rejected = False
                        if name in EMAIL_DRAFT_TOOLS:
                            runtime.email_draft_attempted = True
                        if name in TERMINAL and len(requests) != 1:
                            raise ValueError("Use a terminal tool alone after observations")
                        if name in (
                            set(CALENDAR_READ_TOOLS)
                            | {
                                "prepare_workflow",
                                "answer_question",
                                "read_calendar",
                                "check_day_availability",
                                "retry_calendar_read",
                            }
                        ) and conversation_guard.requires_preparation(runtime):
                            raise conversation_guard.CalendarPreparationRequired
                        arguments = TOOLS[name][0].model_validate(values)
                        key = digest({"tool": name, "input": arguments.model_dump(mode="json")})
                        if name != "respond" and key in seen:
                            raise ValueError(
                                "Repeated call; use existing observation or explain limitation"
                            )
                        # A rejected terminal response is not an observation. Let
                        # the model retry it and receive the actual validation
                        # reason, even when it repeats the same proposed text.
                        if name == "respond":
                            outcome = await conversation_guard.respond(runtime, arguments)
                            if outcome is None:
                                if (
                                    hasattr(runtime, "request")
                                    and email_draft.requires_preparation(runtime)
                                    and (
                                        arguments.kind != "clarification"
                                        or getattr(runtime, "state", {}).get(email_draft.KEY)
                                    )
                                ):
                                    raise email_draft.EmailDraftRequired
                                if email_review.unsupported_promise(arguments.text) and (
                                    getattr(runtime, "state", {}).get(email_draft.KEY)
                                    or getattr(runtime, "artifact", None)
                                ):
                                    outcome = await email_review.review(runtime)
                                else:
                                    mail_goal.require_prepared_reply(runtime, arguments)
                                    bounded = mail_goal.finish_search(runtime, arguments)
                                    outcome = bounded or validate_response(arguments, runtime)
                                    mail_goal.align_cards(runtime)
                        else:
                            outcome = await runtime.call(name, arguments)
                        if name != "respond":
                            seen.add(key)
                        trace.append({"tool": name, "status": outcome.get("error_code", "ok")})
                        if name in TERMINAL:
                            return {**outcome, "release": RELEASE, "trace": trace}
                        if name == "search_mail":
                            last_verified_evidence = []
                        result = {"json": outcome}
                        status = "success"
                    except event_draft.IncompleteEventTitle as exc:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_title_incomplete",
                            }
                        )
                        result = {
                            "json": {
                                "error": "calendar_title_incomplete",
                                "message": "Copy the complete user-supplied title after 'for'. "
                                "Keep its final words, even words that also name commands. "
                                "Repair the tool call; do not ask the user to repeat the title.",
                                "expected_title": exc.title,
                            }
                        }
                        status = "error"
                    except conversation_guard.CalendarNewGoalRequired:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_new_goal_required",
                            }
                        )
                        result = {
                            "json": {
                                "error": "calendar_new_goal_required",
                                "message": (
                                    "Resume only fills missing fields; it cannot replace an "
                                    "existing event's details. Interpret the USER's goal: "
                                    "a new event uses continue_previous=false and create intent, "
                                    "extracting fresh fields without inheritance; a correction "
                                    "uses revise intent and explicit field changes."
                                ),
                            }
                        }
                        status = "error"
                    except calendar_intent.IntentRequired:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_intent_required",
                            }
                        )
                        result = {
                            "json": {
                                "error": "calendar_intent_required",
                                "message": "Supply intent.operation=create/resume/revise/cancel "
                                "and "
                                "intent.source copied from the complete current USER directive. "
                                "Choose the operation semantically, regardless of word order. "
                                "Preserve the supplied fields; repair the tool call yourself.",
                            }
                        }
                        status = "error"
                    except calendar_intent.IntentNotAuthorized:
                        runtime.calendar_event_rejected = True
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_intent_not_authorized",
                            }
                        )
                        return {
                            "kind": "clarification",
                            "text": "Do you want an event created, or only help with that text?",
                            "release": RELEASE,
                            "trace": trace,
                        }
                    except event_draft.FieldRepairRequired as exc:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": exc.code,
                                "field": exc.field,
                            }
                        )
                        result = {
                            "json": {
                                "error": exc.code,
                                "field": exc.field,
                                "message": (
                                    "Repair the date interpretation to match date_source. "
                                    "For weekday wording use kind=weekday with Monday=0 through "
                                    "Sunday=6 and the stated this/next/upcoming week; let the "
                                    "backend resolve it using the saved local anchor. Preserve "
                                    "the other fields. Do not ask the user to repeat the date."
                                    if exc.field == "date"
                                    and exc.code.endswith("interpretation_mismatch")
                                    else "Repair this event field in prepare_calendar_event. "
                                    "Copy its source exactly from the current USER directive, "
                                    "or use email_source field quotes from this turn's owned "
                                    "read_email result for an explicitly requested email event; "
                                    "normalized clock values must match that source. "
                                    "Keep the other grounded fields. For a new event, leave "
                                    "unsupplied fields empty (including title) so the tool asks "
                                    "only for missing information. Do not inherit an older "
                                    "event's fields. For a retained event, preserve its saved "
                                    "fields and send only user-supplied changes. Do not ask "
                                    "the user to repair extraction or repeat supplied details."
                                ),
                            }
                        }
                        status = "error"
                    except event_draft.IntentSourceMismatch:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_intent_source_mismatch",
                            }
                        )
                        result = {
                            "json": {
                                "error": "calendar_intent_source_mismatch",
                                "message": (
                                    "Repair intent.source by copying the complete current "
                                    "user_turn directive, including its greeting, polite prefix "
                                    "and punctuation. "
                                    "Do not shorten, paraphrase or use a prior turn. Exclude any "
                                    "separately quoted email/source body. Keep the other grounded "
                                    "event fields and call prepare_calendar_event again. "
                                    "Do not ask the user to repair a tool field."
                                ),
                            }
                        }
                        status = "error"
                    except IncompleteSearchCoverage:
                        # Citation validation precedes coverage validation. The
                        # fallback can therefore retain these exact verified
                        # quotes without retaining an unsupported ranking claim.
                        last_verified_evidence = [
                            citation.model_dump() for citation in arguments.evidence
                        ]
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "incomplete_search_coverage",
                            }
                        )
                        result = {
                            "json": {
                                "error": "incomplete_search_coverage",
                                "message": (
                                    "Search coverage is incomplete. Say 'the latest I found "
                                    "in this search/date window', or report the dated result "
                                    "without claiming it is the user's global latest."
                                ),
                            }
                        }
                        status = "error"
                    except UninspectedNewerResults as exc:
                        last_verified_evidence = [
                            citation.model_dump() for citation in arguments.evidence
                        ]
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "uninspected_newer_results",
                            }
                        )
                        result = {
                            "json": {
                                "error": "uninspected_newer_results",
                                "message": (
                                    "Before identifying a latest result, inspect the newer "
                                    "returned cards: "
                                    + ", ".join(exc.references[:5])
                                    + ". Read those references (a batch is allowed) "
                                    "before answering, or ask a clarification without "
                                    "claiming which order is latest."
                                ),
                            }
                        }
                        status = "error"
                    except UnsupportedClarificationClaim:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "clarification_claims_source_fact",
                            }
                        )
                        result = {
                            "json": {
                                "error": "clarification_claims_source_fact",
                                "message": (
                                    "A source-free clarification should ask only for "
                                    "missing information. Remove the order ranking, or "
                                    "read and cite the source before answering."
                                ),
                            }
                        }
                        status = "error"
                    except conversation_guard.CalendarPreparationRequired:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "calendar_preparation_required",
                            }
                        )
                        result = {
                            "json": {
                                "error": "calendar_preparation_required",
                                "message": (
                                    "Use prepare_calendar_event for this Calendar request. "
                                    "Preserve the supplied title, date and time; ask only for "
                                    "missing fields through that tool. Use continue_previous=true "
                                    "for a pending event answer. A prose reply cannot establish "
                                    "that an event was queued or created."
                                ),
                            }
                        }
                        status = "error"
                    except email_draft.EmailDraftRequired:
                        trace.append(
                            {"tool": name, "status": "invalid", "reason": "email_draft_required"}
                        )
                        result = {
                            "json": {
                                "error": "email_draft_required",
                                "message": "Use start_email_draft for new composition or "
                                "continue_email_draft with the owned goal_id "
                                "for an answer/revision. "
                                "Copy recipient and purpose from USER text, retaining pending "
                                "fields on follow-ups. Leave missing fields empty; do not ask "
                                "for a subject or exact address just to compose. When both "
                                "are known, supply the generated subject/body in draft. "
                                "Never show this diagnostic to the user.",
                            }
                        }
                        status = "error"
                    except DraftWorkflowRequired:
                        trace.append(
                            {"tool": name, "status": "invalid", "reason": "draft_workflow_required"}
                        )
                        result = {
                            "json": {
                                "error": "draft_workflow_required",
                                "message": (
                                    "The user has supplied a compose goal and one recipient. "
                                    "Prepare an unreviewed draft with "
                                    "prepare_workflow(intent=compose) "
                                    "and the current user_recipient_refs. Optional wording details "
                                    "and Gmail send permission are not required to draft."
                                ),
                            }
                        }
                        status = "error"
                    except IncorrectCapabilityAdvice:
                        trace.append(
                            {
                                "tool": name,
                                "status": "invalid",
                                "reason": "send_reconnect_incorrect",
                            }
                        )
                        result = {
                            "json": {
                                "error": "send_reconnect_incorrect",
                                "message": (
                                    "Gmail sending is disabled by server controls; reconnecting "
                                    "cannot enable it. Drafting is available without "
                                    "send permission."
                                ),
                            }
                        }
                        status = "error"
                    except FreshSearchRequired:
                        trace.append(
                            {"tool": name, "status": "invalid", "reason": "fresh_search_required"}
                        )
                        search_failed = getattr(
                            runtime, "fresh_search_attempted", False
                        ) and not getattr(runtime, "fresh_search_done", False)
                        result = {
                            "json": {
                                "error": "fresh_search_required",
                                "message": (
                                    "The current Gmail search did not complete. Do not claim "
                                    "which messages exist; say you couldn't check just now "
                                    "or ask a source-free clarification."
                                    if search_failed
                                    else "This is a new mailbox request. Use search_mail for the "
                                    "current sender or unfiltered Inbox scope before answering; "
                                    "earlier mail references and counts are not current evidence."
                                ),
                            }
                        }
                        status = "error"
                    except MissingSourceEvidence:
                        trace.append(
                            {"tool": name, "status": "invalid", "reason": "citation_required"}
                        )
                        result = {
                            "json": {
                                "error": "citation_required",
                                "message": (
                                    "Read the relevant email with read_email or "
                                    "read_search_results, then cite an exact short quote "
                                    "and its reference before making an inbox claim. "
                                    "If no fact can be verified, use kind=clarification "
                                    "to ask for a narrower search without asserting mail facts."
                                ),
                            }
                        }
                        status = "error"
                    except UnverifiedSourceEvidence:
                        trace.append(
                            {"tool": name, "status": "invalid", "reason": "citation_unverified"}
                        )
                        result = {
                            "json": {
                                "error": "citation_unverified",
                                "message": (
                                    "Use the same reference and an exact contiguous quote "
                                    "from a read_email or read_search_results observation. "
                                    "A search-card snippet alone is not evidence."
                                ),
                            }
                        }
                        status = "error"
                    except mail_goal.MailRepair as exc:
                        trace.append({"tool": name, "status": "invalid", "reason": exc.code})
                        result = {
                            "json": {
                                "error": exc.code,
                                "message": exc.message,
                                "references": exc.references,
                            }
                        }
                        status = "error"
                    except (ValidationError, ValueError) as exc:
                        if name in EMAIL_DRAFT_TOOLS:
                            repair = email_repair.observation(runtime, name, values, exc)
                            reason = repair["error"]
                            trace.append(
                                {
                                    "tool": name,
                                    "status": "invalid",
                                    "reason": reason,
                                    "fields": repair["fields"],
                                }
                            )
                            email_errors[reason] = email_errors.get(reason, 0) + 1
                            if email_errors[reason] >= 2 or sum(email_errors.values()) >= 3:
                                return {
                                    **email_repair.exhausted(),
                                    "release": RELEASE,
                                    "trace": trace,
                                }
                            result = {"json": repair}
                        elif name == "review_conversation_goal":
                            trace.append({"tool": name, "status": "invalid"})
                            result = {
                                "json": {
                                    "error": "review_goal_required",
                                    "message": (
                                        "Use review_conversation_goal with the intended owned "
                                        "goal_id and complete current USER source. "
                                        "Ask which request if ambiguous; do not invent a target."
                                    ),
                                }
                            }
                        elif name == "prepare_calendar_event":
                            # Pydantic errors contain private input. Keep only allowlisted
                            # field names; never serialize its error text or input values.
                            fields = sorted(
                                {
                                    str(error["loc"][0])
                                    for error in (
                                        exc.errors() if isinstance(exc, ValidationError) else []
                                    )
                                    if error["loc"]
                                    and error["loc"][0] in TOOLS[name][0].model_fields
                                }
                            )
                            trace.append(
                                {
                                    "tool": name,
                                    "status": "invalid",
                                    "reason": "calendar_event_invalid_input",
                                    "fields": fields,
                                }
                            )
                            result = {
                                "json": {
                                    "error": "calendar_event_invalid_input",
                                    "fields": fields,
                                    "message": (
                                        "Repair prepare_calendar_event using its declared schema. "
                                        "Use create intent and continue_previous=false for a new "
                                        "event; use changes only for a retained event revision. "
                                        + (
                                            "Use email_source with exact event/field quotes from "
                                            "a fresh owned read_email result. "
                                            if values.get("email_source")
                                            else "Include exact USER wording with each date/time. "
                                        )
                                        + "Keep grounded fields. Leave missing fields empty; "
                                        "do not repeat an identical rejected call."
                                    ),
                                }
                            }
                        else:
                            trace.append(
                                {"tool": name if name in TOOLS else "unknown", "status": "invalid"}
                            )
                            message = (
                                "The requested Calendar window is unsupported or combines "
                                "periods. Ask the user to choose today, tomorrow, this week, "
                                "or the next 7 days. Do not claim Calendar facts."
                                if name in {"read_calendar", "check_day_availability"}
                                else "Use declared schema and user-supplied search terms. "
                                "Use valid references and exact quotes from read_email."
                            )
                            result = {
                                "json": {
                                    "error": "invalid_tool_input",
                                    "message": message,
                                }
                            }
                        status = "error"
                    except ApiError as exc:
                        if name in EMAIL_DRAFT_TOOLS:
                            email_errors[exc.code] = email_errors.get(exc.code, 0) + 1
                            trace.append({"tool": name, "status": "invalid", "reason": exc.code})
                            if email_errors[exc.code] >= 2 or sum(email_errors.values()) >= 3:
                                return {
                                    **email_repair.exhausted(),
                                    "release": RELEASE,
                                    "trace": trace,
                                }
                            result = {"json": email_repair.observation(runtime, name, values, exc)}
                            status = "error"
                        else:
                            trace.append({"tool": name, "status": exc.code})
                            result = {"json": {"error": exc.code, "message": exc.message}}
                            status = "error"
                        if (
                            name == "prepare_calendar_event"
                            and exc.code == "calendar_event_already_dispatched"
                        ):
                            runtime.calendar_event_rejected = True
                    results.append(
                        {
                            "toolResult": {
                                "toolUseId": call["toolUseId"],
                                "content": [result],
                                "status": status,
                            }
                        }
                    )
                messages.append({"role": "user", "content": results})
                if len(json.dumps(messages)) > 85000:
                    break
    except ConversationProviderError:
        if retained := mail_goal.incomplete(runtime) or mail_goal.search_incomplete(runtime):
            return {**retained, "release": RELEASE, "trace": trace}
        raise ApiError(
            503,
            "conversation_provider_unavailable",
            "The model is temporarily unavailable. Retry this message.",
        ) from None
    except (ProviderError, TimeoutError):
        calendar_failure = conversation_guard.exhausted(runtime)
        if calendar_failure:
            return {**calendar_failure, "release": RELEASE, "trace": trace}
        if retained := mail_goal.incomplete(runtime) or mail_goal.search_incomplete(runtime):
            return {**retained, "release": RELEASE, "trace": trace}
        raise ApiError(
            503, "conversation_unavailable", "I couldn’t finish that response. Retry this message."
        ) from None
    calendar_failure = conversation_guard.exhausted(runtime)
    if calendar_failure:
        return {**calendar_failure, "release": RELEASE, "trace": trace}
    if retained := mail_goal.incomplete(runtime) or mail_goal.search_incomplete(runtime):
        return {**retained, "release": RELEASE, "trace": trace}
    if (
        getattr(runtime, "email_draft_attempted", False)
        and getattr(runtime, "state", {}).get(email_draft.KEY)
        and not getattr(runtime, "loaded", None)
    ):
        return {
            "kind": "message",
            "text": "I couldn’t finish the draft card yet. "
            "I’ve kept your supplied details; please ask me to try again. "
            "This chat turn did not save anything in Gmail or send an email.",
            "error_code": "email_draft_not_prepared",
            "release": RELEASE,
            "trace": trace,
        }
    # A search can reach the finite tool or transcript budget after returning
    # useful cards. Preserve those bounded results instead of turning a
    # recoverable discovery request into an HTTP error. Never infer a fact from
    # unread mail or claim the search covered the entire mailbox.
    page = getattr(runtime, "search_page", None)
    if isinstance(page, dict):
        visible = {row.get("reference") for row in page.get("results", []) if isinstance(row, dict)}
        fallback_evidence = [
            citation
            for citation in last_verified_evidence
            if citation["reference"] in visible
            and " ".join(citation["quote"].split())
            in " ".join(runtime.evidence.get(citation["reference"], "").split())
        ]
        if page.get("results"):
            text = (
                "I found matching emails but couldn't finish checking them in this "
                "request. The email cards below are from this search; choose one to "
                "ask about it, or narrow the search by date or sender."
            )
        else:
            text = (
                "I couldn't finish checking this bounded email search. Try a more "
                "specific term, date or sender."
            )
        return {
            "kind": "message",
            "text": text,
            "evidence": fallback_evidence,
            "release": RELEASE,
            "trace": trace,
        }
    if (
        getattr(runtime, "fresh_search_scope", None)
        and getattr(runtime, "fresh_search_attempted", False)
        and not getattr(runtime, "fresh_search_done", False)
    ):
        return {
            "kind": "message",
            "text": "I couldn't check your inbox right now. Please try again.",
            "evidence": [],
            "release": RELEASE,
            "trace": trace,
        }
    raise ApiError(
        422,
        "conversation_tool_limit",
        "I reached the limit for this request. Try a smaller question.",
    )


def _merge_prepare_workflows(requests):
    """Collapse provider-split terminal calls without granting new authority."""
    workflows = [PrepareWorkflow.model_validate(call["input"]) for call in requests]
    supporting = {tuple(workflow.context_references) for workflow in workflows}
    if len(supporting) != 1:
        raise ValueError("A compound workflow must keep one supporting evidence plan")
    scopes = {workflow.source_scope for workflow in workflows}
    if len(scopes) != 1:
        raise ValueError("A compound workflow must keep one source scope")
    references = {workflow.reference for workflow in workflows if workflow.reference is not None}
    if len(references) > 1:
        raise ValueError("A compound workflow must use one source reference")

    roles = {"to": [], "cc": [], "bcc": []}
    membership = {}
    for role in roles:
        field = role + "_refs"
        for workflow in workflows:
            for reference in getattr(workflow, field):
                previous = membership.setdefault(reference, role)
                if previous != role:
                    raise ValueError("A recipient reference cannot have conflicting roles")
                if reference not in roles[role]:
                    roles[role].append(reference)

    intents = [workflow.intent for workflow in workflows]
    # Scheduling owns the compound coordinator route. For non-calendar compounds,
    # retain a draft-producing intent where present so reply/compose bindings survive.
    intent = next(
        (
            candidate
            for candidate in ("plan_schedule", "reply", "compose", "other", "summarise")
            if candidate in intents
        ),
        intents[0],
    )
    return PrepareWorkflow(
        intent=intent,
        reference=next(iter(references), None),
        compound=True,
        source_scope=scopes.pop(),
        context_references=list(supporting.pop()),
        to_refs=roles["to"],
        cc_refs=roles["cc"],
        bcc_refs=roles["bcc"],
    )


def _tool_error(tool_use_id, code, message):
    return {
        "toolResult": {
            "toolUseId": tool_use_id,
            "content": [{"json": {"error": code, "message": message}}],
            "status": "error",
        }
    }


def validate_response(answer: Respond, runtime):
    if getattr(runtime, "ready_compose_goal", lambda: False)():
        raise DraftWorkflowRequired("Prepare the requested draft")
    capabilities = getattr(runtime, "capabilities", None) or {}
    send = next(
        (item for item in capabilities.get("capabilities", []) if item.get("id") == "gmail_send"),
        None,
    )
    if (
        send
        and send.get("status") == "disabled"
        and re.search(r"\b(?:send|sending)\b", answer.text, re.I)
        and re.search(r"\b(?:reconnect|sign in again)\b", answer.text, re.I)
    ):
        raise IncorrectCapabilityAdvice("Reconnect cannot enable disabled sending")
    if getattr(runtime, "fresh_search_scope", None):
        if not getattr(runtime, "fresh_search_attempted", False):
            raise FreshSearchRequired("Search the latest explicit mailbox scope first")
        if not getattr(runtime, "fresh_search_done", False):
            if not answer.evidence and (
                (answer.kind == "clarification" and _asks_only_for_details(answer.text))
                or (answer.kind == "message" and _reports_search_unavailable(answer.text))
            ):
                return {"kind": answer.kind, "text": answer.text, "evidence": []}
            raise FreshSearchRequired("The current mailbox search did not complete")
    for source in answer.evidence:
        text = runtime.evidence.get(source.reference)
        if not text or " ".join(source.quote.split()) not in " ".join(text.split()):
            raise UnverifiedSourceEvidence("Unverified evidence")
    if runtime.evidence and answer.kind != "clarification" and not answer.evidence:
        raise MissingSourceEvidence("Cite read evidence for source-based advice")
    if (
        answer.kind == "clarification"
        and not answer.evidence
        and not _asks_only_for_details(answer.text)
    ):
        raise UnsupportedClarificationClaim("An uncited clarification asserts a source fact")
    state = getattr(runtime, "state", {})
    has_search_context = isinstance(getattr(runtime, "search_page", None), dict) or (
        isinstance(state, dict) and bool(state.get("result_order"))
    )
    request = getattr(runtime, "request", None)
    instruction = getattr(request, "instruction", None) or getattr(runtime, "instruction", "")
    latest_mail_request = bool(
        re.search(rf"\b{_RECENCY}\b", instruction, re.I)
        and re.search(rf"\b{_INBOX_ITEM}\b", instruction, re.I)
    )
    direct_clarification = answer.kind == "clarification" and _asks_only_for_details(answer.text)
    if (
        not answer.evidence
        and not direct_clarification
        and (
            _asserts_concrete_inbox_rank(answer.text)
            or ((has_search_context or latest_mail_request) and _asserts_inbox_rank(answer.text))
        )
    ):
        raise MissingSourceEvidence("Read and cite an email before asserting its inbox rank")
    unread_newer = _uninspected_newer_results(answer, runtime)
    if unread_newer:
        raise UninspectedNewerResults(unread_newer)
    coverage = _response_search_coverage(runtime)
    if coverage.get("complete") is False and overclaims_incomplete_search(answer.text):
        raise IncompleteSearchCoverage("Qualify recency to the bounded search")
    return {
        "kind": answer.kind,
        "text": answer.text,
        "evidence": [x.model_dump() for x in answer.evidence],
    }


def _reports_search_unavailable(text):
    """Allow only a bounded availability report when no fresh Gmail page exists."""
    value = " ".join(text.split())
    return bool(
        re.fullmatch(
            r"(?:I (?:couldn't|could not|can't|cannot|was unable to) "
            r"(?:search|check|access|load|fetch|connect to|reach) "
            r"(?:Gmail|your (?:inbox|mailbox|email|emails|messages))|"
            r"(?:Gmail|inbox|mailbox|email search) (?:is )?"
            r"(?:temporarily )?(?:unavailable|not available))"
            r"(?: (?:right now|just now|at the moment))?[.!]?"
            r"(?: (?:Please )?try again(?: later| shortly)?[.!]?)?",
            value,
            re.I,
        )
    )


def _uninspected_newer_results(answer, runtime):
    if not answer.evidence:
        return []
    request = getattr(runtime, "request", None)
    instruction = getattr(request, "instruction", None) or getattr(runtime, "instruction", "")
    # For a latest request, even an unranked answer about an older hit can
    # distract from an unread newer order. A follow-up that itself ranks a
    # searched item needs the same inspection regardless of the current turn.
    asks_for_latest = bool(
        re.search(r"\b(?:latest|newest|most\s+recent)\b", instruction, re.I)
    ) or bool(getattr(runtime, "state", {}).get(mail_goal.KEY, {}).get("latest"))
    if not asks_for_latest and not _asserts_inbox_rank(answer.text):
        return []
    cited = {citation.reference for citation in answer.evidence}
    observed = getattr(runtime, "evidence", {})
    page = getattr(runtime, "search_page", None)
    if isinstance(page, dict):
        rows = page.get("results", [])
        cited_times = [
            timestamp
            for row in rows
            if isinstance(row, dict) and row.get("reference") in cited
            if (timestamp := _search_received_at(row.get("received_at"))) is not None
        ]
        if cited_times:
            oldest_cited = min(cited_times)
            return [
                row["reference"]
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("reference"), str)
                and row["reference"] not in observed
                and (timestamp := _search_received_at(row.get("received_at"))) is not None
                and timestamp > oldest_cited
            ]
    # Search cards are transient; a follow-up turn retains their reference order
    # but not their text or timestamps. Require earlier displayed references to
    # be inspected before ranking a later one.
    state = getattr(runtime, "state", {})
    order = state.get("result_order", []) if isinstance(state, dict) else []
    cited_positions = [index for index, reference in enumerate(order) if reference in cited]
    if not cited_positions:
        return []
    return [reference for reference in order[: max(cited_positions)] if reference not in observed]


def _search_received_at(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        return instant.timestamp()
    except ValueError:
        return None


_RECENCY = r"(?:latest|newest|most\s+recent)"
_INBOX_ITEM = (
    r"(?:e-?mails?|mail|messages?|threads?|orders?|receipts?|invoices?|bookings?|"
    r"confirmations?|purchases?)"
)
_ABSOLUTE_INBOX_RANK = re.compile(
    rf"\b(?:your|the)\s+{_RECENCY}\s+(?:[\w'-]+\s+){{0,4}}{_INBOX_ITEM}\b|"
    rf"^\s*{_RECENCY}\s+(?:[\w'-]+\s+){{0,4}}{_INBOX_ITEM}\b",
    re.I,
)
_PREDICATIVE_ABSOLUTE_RANK = re.compile(
    rf"\b{_INBOX_ITEM}\b(?:\s+[#\w-]+){{0,2}}\s+"
    rf"(?:is|was|are|were|appears?\s+to\s+be|seems?\s+to\s+be)\s+"
    rf"(?:definitely\s+|probably\s+)?(?:your|the)\s+{_RECENCY}\b",
    re.I,
)
_UNCITED_PREDICATIVE_RANK = re.compile(
    rf"\b{_INBOX_ITEM}\b(?:\s+[#\w-]+){{0,2}}\s+"
    rf"(?:is|was|are|were|appears?\s+to\s+be|seems?\s+to\s+be)\s+"
    rf"(?:definitely\s+|probably\s+)?(?:(?:your|the)\s+)?{_RECENCY}\b",
    re.I,
)


def _asserts_concrete_inbox_rank(text):
    """Require a current read for concrete inbox rankings, even across turns."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip()
        if (sentence.endswith("?") and _QUESTION_START.match(sentence)) or (
            _DETAIL_REQUEST_START.match(sentence)
        ):
            continue
        if _ABSOLUTE_INBOX_RANK.search(sentence) or _UNCITED_PREDICATIVE_RANK.search(sentence):
            return True
    return False


def _asserts_inbox_rank(text):
    """Conservatively catch recency language in a cited search answer.

    This is only a trigger for inspecting newer returned cards. It does not
    decide whether an email is an order or whether the whole inbox was searched.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if sentence.strip().endswith("?") and re.match(
            r"\s*(?:what|which|when|can|could|do|did|is|are|would|will)\b",
            sentence,
            re.I,
        ):
            continue
        if re.search(rf"\b{_RECENCY}\b", sentence, re.I):
            return True
    return False


_QUESTION_START = re.compile(
    r"(?:what|which|when|where|who|whose|why|how|can|could|would|will|"
    r"do|does|did|is|are|was|were|should|may)\b",
    re.I,
)
_DETAIL_REQUEST_START = re.compile(
    r"please\s+(?:clarify|narrow|provide|select|choose|share|specify|"
    r"tell|request|try)\b",
    re.I,
)


def _asks_only_for_details(text):
    """Keep uncited clarifications to questions or direct detail requests."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]

    def detail_request(part):
        if not _DETAIL_REQUEST_START.match(part):
            return False
        if re.match(r"please\s+clarify\s+(?:whether|if)\b", part, re.I):
            return not re.search(r"[:,;]|\b(?:i|we)\s+(?:found|read|confirmed)\b", part, re.I)
        # A short imperative can request missing input, but cannot smuggle a
        # declarative mail finding after punctuation or a finite verb.
        return not re.search(
            rf"[:,;]|\b{_RECENCY}\b|\b(?:is|was|are|were|has|have|had|found|confirmed)\b",
            part,
            re.I,
        )

    return bool(sentences) and all(
        (part.endswith("?") and _QUESTION_START.match(part)) or detail_request(part)
        for part in sentences
    )


_SEARCH_SCOPE = (
    r"(?:in|among|from|within|of)\s+(?:this|the|these|those)?\s*"
    r"(?:search|results?|matches?|date\s+window)|"
    r"based\s+on\s+(?:this|the|these|those)?\s*(?:search|results?|matches?)|"
    r"(?:emails?|messages?)\s+(?:(?:i|we)\s+)?"
    r"(?:returned|reviewed|saw|shown|checked|searched)"
)


def overclaims_incomplete_search(text, user_turn=None):
    """Reject only unscoped absolute ranking of inbox items."""
    del user_turn  # Compatibility for the deterministic evaluation helper.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        predicative = _PREDICATIVE_ABSOLUTE_RANK.search(sentence)
        if predicative:
            before = sentence[max(0, predicative.start() - 120) : predicative.start()]
            after = sentence[predicative.end() : predicative.end() + 100]
            if not re.search(rf"(?:{_SEARCH_SCOPE})\b", before, re.I) and not re.search(
                rf"(?:{_SEARCH_SCOPE})\b", after, re.I
            ):
                return True
        claim = _ABSOLUTE_INBOX_RANK.search(sentence)
        if not claim:
            continue
        prefix = sentence[max(0, claim.start() - 120) : claim.start()]
        if re.search(rf"(?:{_SEARCH_SCOPE})\b.{{0,100}}$", prefix, re.I):
            continue
        before_copula = re.split(
            r"\b(?:is|was|are|were)\b", sentence[claim.start() :], maxsplit=1, flags=re.I
        )[0]
        if re.search(
            r"\b(?:that\s+)?(?:i|we)\s+"
            r"(?:found|located|saw|could\s+find|can\s+find)\b",
            before_copula,
            re.I,
        ) or re.search(rf"(?:{_SEARCH_SCOPE})\b", before_copula, re.I):
            continue
        return True
    return False


def _response_search_coverage(runtime):
    page = getattr(runtime, "search_page", None)
    if isinstance(page, dict):
        return page.get("coverage", {})
    state = getattr(runtime, "state", {})
    if not isinstance(state, dict):
        return {}
    result_references = set(state.get("result_order", []))
    if not result_references.intersection(getattr(runtime, "evidence", {})):
        return {}
    search = state.get("search", {})
    return search.get("coverage", {}) if isinstance(search, dict) else {}
