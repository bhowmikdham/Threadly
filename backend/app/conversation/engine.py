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


class MissingSourceEvidence(ValueError):
    """A source-based answer omitted citations, including an unread ranking claim."""


class UnverifiedSourceEvidence(ValueError):
    """A response cited a reference or quote outside the read observations."""


class UninspectedNewerResults(ValueError):
    """A latest-result answer skipped newer returned search candidates."""

    def __init__(self, references):
        self.references = references
        super().__init__("Newer search results remain unread")


class UnsupportedClarificationClaim(ValueError):
    """An uncited clarification asserts an inbox ranking instead of only asking."""


async def run(context, runtime, model=None):
    messages = [{"role": "user", "content": [{"text": json.dumps(context)}]}]
    seen, calls, trace = set(), 0, []
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
                        if name != "respond" and key in seen:
                            raise ValueError(
                                "Repeated call; use existing observation or explain limitation"
                            )
                        # A rejected terminal response is not an observation. Let
                        # the model retry it and receive the actual validation
                        # reason, even when it repeats the same proposed text.
                        if name != "respond":
                            seen.add(key)
                        if name == "respond":
                            outcome = validate_response(arguments, runtime)
                        else:
                            outcome = await runtime.call(name, arguments)
                        trace.append({"tool": name, "status": "ok"})
                        if name in TERMINAL:
                            return {**outcome, "release": RELEASE, "trace": trace}
                        if name == "search_mail":
                            last_verified_evidence = []
                        result = {"json": outcome}
                        status = "success"
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


def _uninspected_newer_results(answer, runtime):
    if not answer.evidence:
        return []
    request = getattr(runtime, "request", None)
    instruction = getattr(request, "instruction", None) or getattr(runtime, "instruction", "")
    # For a latest request, even an unranked answer about an older hit can
    # distract from an unread newer order. A follow-up that itself ranks a
    # searched item needs the same inspection regardless of the current turn.
    asks_for_latest = bool(re.search(r"\b(?:latest|newest|most\s+recent)\b", instruction, re.I))
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
