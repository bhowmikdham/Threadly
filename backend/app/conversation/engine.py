"""Finite read/decision loop, independent of transport and persistence."""

import asyncio
import json
import logging
import random
import re

from pydantic import ValidationError

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.conversation.prompt import PROMPT, RELEASE
from app.model_client.conversation import ConversationModel, ConversationProviderError
from app.model_client.providers import ProviderError
from app.schemas.conversation import TOOLS, PrepareWorkflow, Respond, tool_config

TERMINAL = {"respond", "prepare_workflow", "answer_question", "revise_draft"}
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


async def run(context, runtime, model=None):
    messages = [{"role": "user", "content": [{"text": json.dumps(context)}]}]
    seen, calls, trace = set(), 0, []
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
                        arguments = _merge_prepare_workflows(requests)
                        key = digest({"tool": "prepare_workflow", "input": arguments.model_dump()})
                        if key in seen:
                            raise ValueError("Repeated compound workflow call")
                        seen.add(key)
                        outcome = await runtime.call("prepare_workflow", arguments)
                        trace.append({"tool": "prepare_workflow", "status": "ok"})
                        return {**outcome, "release": RELEASE, "trace": trace}
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
                        if name in TERMINAL and len(requests) != 1:
                            raise ValueError("Use a terminal tool alone after observations")
                        arguments = TOOLS[name][0].model_validate(values)
                        key = digest({"tool": name, "input": arguments.model_dump(mode="json")})
                        if key in seen:
                            raise ValueError(
                                "Repeated call; use existing observation or explain limitation"
                            )
                        seen.add(key)
                        if name == "respond":
                            outcome = validate_response(arguments, runtime)
                        else:
                            outcome = await runtime.call(name, arguments)
                        trace.append({"tool": name, "status": "ok"})
                        if name in TERMINAL:
                            return {**outcome, "release": RELEASE, "trace": trace}
                        result = {"json": outcome}
                        status = "success"
                    except IncompleteSearchCoverage:
                        trace.append({"tool": name, "status": "invalid"})
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
                    except (ValidationError, ValueError):
                        trace.append(
                            {"tool": name if name in TOOLS else "unknown", "status": "invalid"}
                        )
                        result = {
                            "json": {
                                "error": "invalid_tool_input",
                                "message": (
                                    "Use declared schema and user-supplied search terms. "
                                    "Use valid references and exact quotes from read_email."
                                ),
                            }
                        }
                        status = "error"
                    except ApiError as exc:
                        trace.append({"tool": name, "status": exc.code})
                        result = {"json": {"error": exc.code, "message": exc.message}}
                        status = "error"
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
        raise ApiError(
            503,
            "conversation_provider_unavailable",
            "The model is temporarily unavailable. Retry this message.",
        ) from None
    except (ProviderError, TimeoutError):
        raise ApiError(
            503, "conversation_unavailable", "I couldn’t finish that response. Retry this message."
        ) from None
    # A search can reach the finite tool or transcript budget after returning
    # useful cards. Preserve those bounded results instead of turning a
    # recoverable discovery request into an HTTP error. Never infer a fact from
    # unread mail or claim the search covered the entire mailbox.
    page = getattr(runtime, "search_page", None)
    if isinstance(page, dict):
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
    for source in answer.evidence:
        text = runtime.evidence.get(source.reference)
        if not text or " ".join(source.quote.split()) not in " ".join(text.split()):
            raise ValueError("Unverified evidence")
    if runtime.evidence and answer.kind != "clarification" and not answer.evidence:
        raise ValueError("Cite read evidence for source-based advice")
    coverage = _response_search_coverage(runtime)
    if coverage.get("complete") is False and overclaims_incomplete_search(answer.text):
        raise IncompleteSearchCoverage("Qualify recency to the bounded search")
    return {
        "kind": answer.kind,
        "text": answer.text,
        "evidence": [x.model_dump() for x in answer.evidence],
    }


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
