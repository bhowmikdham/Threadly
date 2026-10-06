"""Authorized capabilities. Gmail remains on demand; model cannot execute writes."""

import json
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.errors import ApiError
from app.assistant import (
    command_plans,
    continuation,
    coordinator,
    draft_review,
    inbox_chat,
    source_data,
    tasks,
)
from app.assistant.summary import digest
from app.capabilities.service import build_capabilities
from app.config import get_settings
from app.conversation import calendar_context, mail_context
from app.db.models import CalendarPreference, ContextSnapshot, User
from app.mail.presentation import clock_context, received_display
from app.schemas.assistant import AssistantRequest, DraftOptions
from app.schemas.calendar_tools import CALENDAR_READ_TOOLS
from app.schemas.continuation import TaskInputRequest
from app.schemas.coordinator import CoordinatorRequest
from app.schemas.draft_review import DraftRecipients, EditDraftRequest
from app.schemas.inbox_chat import InboxFilters
from app.schemas.workflow import WorkflowRequest

SEARCH_REFERENCE_LIMIT = 25
SEARCH_READ_BODY_CHARS = 2000
WORKFLOW_BINDING_ERROR = "workflow_binding_invalid"
SCHEDULING_WORKFLOW_REQUIRED = "scheduling_workflow_required"


def scheduling_request(instruction):
    """Distinguish availability work from a read-only existing-events agenda."""

    return bool(
        re.search(
            r"\b(?:free|available|availability|slots?|times? to meet)\b|"
            r"\b(?:schedule|reschedule|book)\b.{0,50}\b(?:meeting|call|appointment)\b",
            instruction,
            re.I,
        )
    )


def fresh_search_scope(latest_turn):
    """Keep an explicit new search separate from older displayed mail references."""

    sender = inbox_chat.explicit_sender_email(latest_turn)
    # A quoted From header in a question about the selected email is source
    # context, not a request to search the mailbox for that sender.
    sender_discovery = (
        re.search(
            r"\b(?:e-?mails?|messages?|threads?|mail)\s+"
            r"(?:(?:i|we|that|which|all|any|the|new|recent|latest|received|got|have|was|were|sent)\s+){0,3}"
            r"(?:from|sent\s+by)\s*:?[ \t]*" + re.escape(sender or ""),
            latest_turn,
            re.I,
        )
        if sender
        else None
    )
    if sender and sender_discovery:
        return {"kind": "sender", "sender_email": sender}
    match = re.search(
        r"\b(?:latest|newest|most\s+recent)\s+([1-5])\s+"
        r"(?:emails?|messages?)\s+(?:in|from)\s+(?:my\s+)?inbox\s*[?.!]*$",
        latest_turn,
        re.I,
    )
    if match:
        return {"kind": "recent_inbox", "limit": int(match[1])}
    return None


def _normalise_words(value):
    return " ".join(value.casefold().split())


def _normalise_action_typos(value):
    """Accept adjacent-letter transpositions in creation verbs, not arbitrary new intents."""

    verbs = ("create", "draft", "write", "compose", "prepare")

    def correct(match):
        word = match.group()
        if word in verbs:
            return word
        for verb in verbs:
            if len(word) == len(verb) and any(
                word == verb[:i] + verb[i + 1] + verb[i] + verb[i + 2 :]
                for i in range(len(verb) - 1)
            ):
                return verb
        return word

    return re.sub(r"\b[a-z]+\b", correct, _normalise_words(value))


EMAIL_ADDRESS = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def _recipient_question(value):
    return bool(
        re.search(
            r"\b(?:email address|recipient address|address for|who (?:should|do) (?:i|we) "
            r"(?:send|email)|who is (?:this|the) (?:email|message) (?:to|for))\b",
            value,
            re.I,
        )
    )


def _user_recipient_entries(user_text, history=(), latest_turn=""):
    """Return user-specified address and role pairs in their stated order."""

    entries = [
        (
            "to" if match.group("role").casefold() == "email" else match.group("role").casefold(),
            match.group("email"),
        )
        for match in re.finditer(
            rf"\b(?P<role>to|cc|bcc|email)\s*:?[ \t]*"
            rf"(?:[A-Za-z][A-Za-z .'-]{{0,45}}[ \t]+at[ \t]+|"
            rf"[A-Za-z][A-Za-z .'-]{{0,45}}[ \t]*[<(][ \t]*|<)?"
            rf"(?P<email>{EMAIL_ADDRESS})\b",
            user_text,
            re.I,
        )
    ]
    if "\nUser follow-up:" in user_text and history:
        chain = _open_compose_chain(history)
        answers = [entry.get("user", "") for entry in chain[1:]] + [latest_turn]
        questions = [entry.get("assistant", "") for entry in chain]
        for question, answer in zip(questions, answers, strict=False):
            if _recipient_question(question):
                entries.extend(("to", address) for address in re.findall(EMAIL_ADDRESS, answer))
    return entries


def user_recipient_references(user_text, history=(), latest_turn=""):
    """Issue handles for user-authored recipient roles, not incidental addresses."""

    addresses = [address for _, address in _user_recipient_entries(user_text, history, latest_turn)]
    addresses = list(dict.fromkeys(addresses))[:20]
    return {f"recipient-{i + 1}": address for i, address in enumerate(addresses)}


def user_recipient_roles(user_text, history=(), latest_turn=""):
    """Record which recipient role each user-authored address can occupy."""

    roles = {role: set() for role in ("to", "cc", "bcc")}
    for role, address in _user_recipient_entries(user_text, history, latest_turn):
        roles[role].add(address)
    return roles


def validate_workflow_bindings(
    args, loaded_references, recipient_references, allowed_recipient_roles=None
):
    """Keep source provenance and recipient authority at the deterministic boundary."""

    if args.reference is not None and args.reference not in loaded_references:
        raise ApiError(
            422,
            WORKFLOW_BINDING_ERROR,
            "Read the email reference before preparing work from it.",
        )
    if args.reference is None and (loaded_references or args.intent in {"summarise", "reply"}):
        raise ApiError(
            422,
            WORKFLOW_BINDING_ERROR,
            "Keep the reference of the email read for this workflow.",
        )
    roles = (args.to_refs, args.cc_refs, args.bcc_refs)
    if any(ref not in recipient_references for refs in roles for ref in refs):
        raise ApiError(
            422,
            WORKFLOW_BINDING_ERROR,
            (
                "Use recipient references only from user_recipient_refs. "
                "Omit recipient references when the user supplied no address."
            ),
        )
    if allowed_recipient_roles is not None and any(
        ref not in allowed_recipient_roles[role]
        for role in ("to", "cc", "bcc")
        for ref in getattr(args, role + "_refs")
    ):
        raise ApiError(
            422,
            WORKFLOW_BINDING_ERROR,
            "Use recipient references only in the To, Cc or Bcc role specified by the user.",
        )


def _duration_values(value):
    """Return only durations stated literally in a user's latest turn."""
    result = set()
    for match in re.finditer(r"\b(\d{1,3})\s*(minutes?|mins?|hours?|hrs?)\b", value, re.I):
        amount = int(match[1])
        result.add(amount * 60 if match[2].casefold().startswith(("hour", "hr")) else amount)
    if re.search(r"\b(?:half an hour|half hour)\b", value, re.I):
        result.add(30)
    if re.search(r"\b(?:an|one) hour\b", value, re.I):
        result.add(60)
    return result


def validate_clarification(answer, latest_turn, question, timezone):
    """Fence model output to typed values entailed by this turn and request context."""
    values = answer.model_dump(exclude_none=True)
    allowed = set(question.get("fields") or [])
    if not values or not set(values).issubset(allowed):
        raise ValueError("Answer fields must match the active question")
    folded = _normalise_words(latest_turn)
    for field, value in values.items():
        if field in {"context_snapshot_id", "reply_message_id"}:
            raise ValueError("Select source identity through a backend reference")
        if field == "recipients":
            if any(_normalise_words(item) not in folded for item in value):
                raise ValueError("Recipient must be explicit in this user turn")
        elif field == "timezone":
            # The browser-supplied IANA zone is trusted context; any other zone must
            # be written literally by the user in this answer.
            if value != timezone and _normalise_words(value) not in folded:
                raise ValueError("Timezone is not supplied by the user or request context")
        elif field == "duration_minutes":
            if value not in _duration_values(latest_turn):
                raise ValueError("Duration must be explicit in this user turn")
        elif field in {"date_phrase", "time_phrase"}:
            if _normalise_words(value) not in folded:
                raise ValueError(f"{field} must be explicit in this user turn")
        elif field == "am_or_pm":
            stated = set()
            if re.search(r"\b(?:a\.?m\.?|morning)\b", latest_turn, re.I):
                stated.add("AM")
            if re.search(r"\b(?:p\.?m\.?|afternoon|evening)\b", latest_turn, re.I):
                stated.add("PM")
            if value not in stated or len(stated) != 1:
                raise ValueError("AM or PM must be unambiguous in this user turn")


def _is_contextual_followup(value):
    return len(value.split()) <= 12 and bool(
        re.match(
            r"\s*(?:yes|yep|sure|ok(?:ay)?|please do|go ahead|do (?:it|that)|"
            r"draft (?:it|that)|reply to (?:it|that)|summari[sz]e (?:it|that)|"
            r"the (?:first|second|third) one)\b",
            value,
            re.I,
        )
    )


def _clarification_is_open(entry):
    """Model questions keep their user-authored goal open even if mislabelled message."""

    return entry.get("kind") == "clarification" or (
        entry.get("kind") == "message" and entry.get("assistant", "").strip().endswith("?")
    )


def _open_compose_chain(history):
    chain = []
    for entry in reversed(history[-12:]):
        if not _clarification_is_open(entry):
            break
        chain.append(entry)
    chain.reverse()
    if not chain:
        return []
    try:
        authorize_workflow(chain[0].get("user", ""), "compose", False)
    except ValueError:
        return []
    return chain


def _revokes_compose_request(value):
    folded = _normalise_words(value)
    return bool(
        (
            re.search(
                r"\b(?:no thanks|never ?mind|cancel(?: it| this)?|stop|changed my mind)\b",
                folded[:100],
            )
            and not re.search(
                r"\b(?:create|draft|write|compose|prepare)\b.{0,70}\b(?:email|e-mail|message)\b",
                folded,
            )
        )
        or re.search(
            r"\b(?:do not|don't|never)\s+(?:create|draft|write|compose|prepare)\b",
            folded,
        )
    )


def _starts_independent_request(value):
    """A new command supersedes a pending question rather than inheriting its recipient."""

    text = _normalise_action_typos(value)
    if re.fullmatch(
        r"\s*(?:(?:yes|yep|sure|ok(?:ay)?|please)\s*[,!.]?\s*){0,2}"
        r"(?:draft|write|compose|prepare|reply to|summari[sz]e)\s+(?:it|that)\s*[.! ]*",
        text,
    ):
        return False
    if (
        _is_contextual_followup(value)
        and not re.search(r"\b(?:new|another|different)\s+(?:email|message|task)\b", text)
        and not re.search(r"\b(?:email|e-mail|message)\s+to\b", text)
    ):
        return False
    if re.search(
        r"\b(?:create|draft|write|compose|prepare|send)\b.{0,80}"
        r"\b(?:email|e-mail|message|note)\b",
        text[:140],
    ):
        return True
    return bool(
        re.search(
            r"\b(?:find|search|show|list|read|summari[sz]e|recap|reply|respond|"
            r"schedule|reschedule|book|plan)\b",
            text,
        )
        and len(text.split()) <= 24
    )


def authoritative_user_instruction(latest_turn, history):
    """Resolve bounded follow-ups using user-authored dialogue only."""

    latest = latest_turn.strip()
    if _revokes_compose_request(latest):
        return latest
    if history and not _starts_independent_request(latest):
        chain = _open_compose_chain(history)
        if chain:
            # A question chain carries the original user goal and all user
            # answers, never assistant text, mail bodies or older addresses.
            turns = [entry.get("user", "").strip() for entry in chain]
            return "\n".join(
                [turns[0]] + [f"User follow-up: {turn}" for turn in [*turns[1:], latest] if turn]
            )
    previous = [entry.get("user", "").strip() for entry in history]
    previous = [value for value in previous if value]
    if previous and _is_contextual_followup(latest):
        return previous[-1] + "\nUser follow-up: " + latest
    return latest


def model_history(history):
    """Hide provider-authored agenda details from later model decisions."""
    return [
        {
            **entry,
            "assistant": "Calendar results were shown. Read Calendar again for current details.",
        }
        if entry.get("source") in {"calendar_agenda", "calendar_availability", "calendar_tools"}
        else entry
        for entry in history
    ]


MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
)


def unsupported_agenda_date(instruction):
    """Reject explicit dates that the bounded day/week agenda cannot represent."""
    return bool(
        re.search(
            r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"weekend|yesterday|tonight|morning|afternoon|evening)\b|"
            r"\b(?:this|next|last)\s+(?:month|year|weekend)\b|"
            r"\b(?:next|last)\s+week\b|"
            r"\b\d{4}-\d{1,2}-\d{1,2}\b|"
            r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|"
            r"\b(?:on\s+(?:the\s+)?|for\s+the\s+)\d{1,2}(?:st|nd|rd|th)?\b|"
            r"\b(?:in|on|for|during|this|next)\s+(?:the\s+)?" + MONTH + r"\b|"
            r"\b" + MONTH + r"\s+(?:\d{1,4}|events?|meetings?|calendar|schedule)\b|"
            r"\b\d{1,2}\s+" + MONTH + r"\b",
            instruction,
        )
    )


def validate_agenda_request(instruction, period):
    """Apply one agenda/scheduling boundary in production and model replay."""

    instruction = instruction.casefold()
    if not re.search(
        r"\b(?:calendar|agenda|events?|meetings?|appointments?|schedule|busy|free|"
        r"available|availability|slots?)\b",
        instruction,
    ):
        raise ValueError("The user did not request Calendar information")
    if scheduling_request(instruction):
        raise ApiError(
            422,
            SCHEDULING_WORKFLOW_REQUIRED,
            "Use prepare_workflow(intent=plan_schedule) for availability or meeting slots. "
            "The scheduling task will ask for missing details if needed.",
        )
    if unsupported_agenda_date(instruction):
        raise ValueError("Ask about an unsupported date or time before reading")
    requested_periods = {
        requested
        for requested, pattern in (
            ("today", r"\btoday\b"),
            ("tomorrow", r"\btomorrow\b"),
            ("this_week", r"\bthis week\b"),
            (
                "next_7_days",
                r"\b(?:next|coming|upcoming)\b.{0,15}\b(?:7 days|seven days|week)\b",
            ),
        )
        if re.search(pattern, instruction)
    }
    if len(requested_periods) > 1:
        raise ValueError("Ask which Calendar period to read")
    if period == "tomorrow" and "tomorrow" not in instruction:
        raise ValueError("Tomorrow was not requested")
    if period == "this_week" and "this week" not in instruction:
        raise ValueError("This week was not requested")
    if period == "next_7_days" and not re.search(
        r"\b(?:next|coming|upcoming)\b.{0,15}\b(?:7 days|seven days|week)\b",
        instruction,
    ):
        raise ValueError("A seven-day window was not requested")
    if period == "today" and re.search(
        r"\b(?:tomorrow|this week|next week|next 7 days)\b", instruction
    ):
        raise ValueError("Use the requested Calendar period")


def authorize_workflow(instruction, intent, compound):
    """Require a user-authored request for every model-selected workflow class."""
    if intent == "compose" and _revokes_compose_request(instruction.splitlines()[-1]):
        raise ValueError("The user cancelled or declined composing")
    value = _normalise_action_typos(instruction)
    operations = set()
    if re.search(r"\b(?:summari[sz]e|summary|recap)\b", value):
        operations.add("summarise")
    if re.search(
        r"(?:^|[.!?]\s*)\s*(?:please\s+)?(?:reply|respond)\b|"
        r"\b(?:can|could|would|will) you (?:please )?(?:help me )?(?:reply|respond)\b|"
        r"\b(?:write|get) back to\b|"
        r"\b(?:draft|write|prepare|compose|create)\b.{0,80}\b(?:reply|response)\b",
        value,
    ):
        operations.add("reply")
    if (
        re.search(
            r"\b(?:draft|write|prepare|compose|create)\b.{0,80}\b(?:email|e-mail|message|note)\b",
            value,
        )
        or re.search(r"\b(?:write|compose)\b.{0,80}\b\S+@\S+", value)
        or re.search(
            r"\b(?:write|message|compose)\s+(?:an?\s+)?(?:to\s+)?"
            r"(?:customer\s+support|support|[a-z][\w.-]{1,60})\b",
            value,
        )
        or re.search(
            r"(?:^|[.!?]\s*|\b(?:can|could|would|will) you\s+)"
            r"(?:please\s+)?email\s+(?:an?\s+)?(?:to\s+)?"
            r"(?:customer\s+support|support|[a-z][\w.-]{1,60})\b",
            value,
        )
    ) and "reply" not in operations:
        operations.add("compose")
    if re.search(
        r"\b(?:schedule|reschedule|book)\b.{0,50}\b(?:meeting|call|time|appointment)\b|"
        r"\b(?:suggest|offer|propose)\b.{0,50}\b"
        r"(?:slots?|meeting times?|times? to meet|availability)\b|"
        r"\b(?:give|find)\s+(?:me\s+)?(?:(?:a|an|some|one|two|three|\d+)\s+)?"
        r"(?:(?:free|available)\s+)?(?:slots?|times? to meet)\b|"
        r"\bfind\s+(?:my|our)\s+availability\b|"
        r"\b(?:are|am|is|will)\b.{0,35}\b(?:free|available)\b|"
        r"\b(?:can|could|should)\s+(?:we|i|you)\s+meet\b|"
        r"\bcheck\b.{0,35}\b(?:calendar|availability|free time)\b",
        value,
    ):
        operations.add("plan_schedule")
    if re.search(r"\b(?:plan|action items?|tasks?|commitments?)\b", value):
        operations.add("other")

    # A read-only single summary may be phrased semantically (for example,
    # "give me a rundown"). Keep the stricter lexical gate for every other
    # workflow and for compound work.
    if intent == "summarise" and not compound and not operations:
        return
    if intent not in operations:
        raise ValueError("The user did not request this workflow intent")
    multi = len(operations & {"summarise", "reply", "compose", "plan_schedule", "other"}) > 1
    if compound and not multi:
        raise ValueError("Compound work requires multiple user-requested operations")
    if multi and not compound:
        raise ValueError("Multiple requested operations require a complete compound proposal")


def proposal_text(state):
    return {
        "planning": "I'm preparing the requested steps.",
        "proposed": "Here is the proposed work for you to review.",
        "failed": "I couldn't prepare this request. Please try again. Nothing was sent or booked.",
        "needs_clarification": "I need a little more detail before preparing this request.",
        "unsupported": "I couldn't prepare all the requested steps. Please revise the request.",
        "expired": "This proposal has expired. Please ask again for an updated result.",
        "consumed": "This proposal has already been continued. Check its task for the result.",
    }.get(state, "Check the request status before continuing.")


class Runtime:
    def __init__(self, owner, request, state, factory, lease=None):
        self.owner, self.request, self.state, self.factory = owner, request, state, factory
        self.lease = lease
        self.calendar_reparse = None
        self.mail_anchor = datetime.now(UTC)
        self.calendar_anchor = (
            datetime.fromisoformat(state["calendar_read_anchor"])
            if state.get("calendar_read_anchor")
            else datetime.now(UTC)
        )
        self.evidence, self.loaded, self.read_scopes = {}, {}, {}
        self.source_selections = {}
        self.last_read_scopes = {}
        self.turn_source_references = []
        self.search_page = None
        self.fresh_search_scope = fresh_search_scope(request.instruction)
        self.fresh_search_done = False
        self.fresh_search_attempted = False
        self.active = self.artifact = None
        # Recipient references are scoped to the current user-authored goal, not
        # every address mentioned earlier in this conversation.
        self.goal_instruction = authoritative_user_instruction(
            request.instruction, state["history"]
        )
        self.recipients = user_recipient_references(
            self.goal_instruction, state["history"], request.instruction
        )
        role_addresses = user_recipient_roles(
            self.goal_instruction, state["history"], request.instruction
        )
        self.recipient_roles = {
            role: {ref for ref, address in self.recipients.items() if address in addresses}
            for role, addresses in role_addresses.items()
        }

    def ready_compose_goal(self):
        """A single-recipient draft goal should enter reviewable workflow, not send advice."""

        if getattr(self, "active", None) or len(getattr(self, "recipients", {})) != 1:
            return False
        if not self.recipient_roles["to"]:
            return False
        if re.match(r"\s*(?:should|would)\s+(?:i|we)\b", self.goal_instruction, re.I):
            return False
        latest = _normalise_words(self.request.instruction)
        if re.fullmatch(r"(?:thanks|thank you)[.! ]*", latest):
            return False
        if _revokes_compose_request(latest):
            return False
        try:
            authorize_workflow(self.goal_instruction, "compose", False)
        except ValueError:
            return False
        return True

    def _reset_previous_search(self):
        # A new explicit mailbox request supersedes old search references. The
        # independently pinned source, if any, remains available to the user.
        mail_context.reset_search(self.state)

    def authoritative_instruction(self):
        """Build workflow input exclusively from bounded user-authored turns.

        Model-authored tool arguments and fetched email bodies are deliberately
        excluded. An open compose clarification carries its original purpose and
        user answers; a short deictic follow-up otherwise receives the immediately
        previous user turn. Neither path grants source email authority.
        """
        return getattr(self, "goal_instruction", None) or authoritative_user_instruction(
            self.request.instruction, self.state["history"]
        )

    def conversation_provenance(self, instruction):
        from app.conversation.prompt import assets

        settings = get_settings()
        return {
            "source": "conversation_user_turns",
            "authority": "user_dialogue_only",
            "conversation_id": self.request.conversation_id,
            "turn_request_id": self.request.request_id,
            "instruction_hash": digest(instruction),
            "provider": settings.inference_provider,
            "model_id": settings.bedrock_model_id,
            **assets(),
        }

    async def context(self):
        if self.fresh_search_scope:
            self._reset_previous_search()
        if "context_snapshot_id" in self.request.model_fields_set:
            if self.request.context_snapshot_id:
                async with self.factory() as session:
                    context = await session.scalar(
                        select(ContextSnapshot).where(
                            ContextSnapshot.id == self.request.context_snapshot_id,
                            ContextSnapshot.user_id == self.owner,
                        )
                    )
                    if not context:
                        raise ApiError(404, "context_not_found", "Select an accessible email.")
                    ui_map = context.payload.get("ui_map")
                    selected = (ui_map or {}).get("selected_message_ids", [])
                    self.state["refs"]["selected"] = {
                        "message_id": selected[0] if len(selected) == 1 else None,
                        "context_id": context.id,
                        "thread_id": context.payload["thread_id"],
                    }
            else:
                self.state["refs"].pop("selected", None)
        if "active_task_id" in self.request.model_fields_set:
            if self.request.active_task_id:
                self.state["active_task_id"] = self.request.active_task_id
                self.state.pop("proposal_id", None)
            else:
                self.state.pop("active_task_id", None)
        async with self.factory() as session:
            user = await session.get(User, self.owner)
            caps = build_capabilities(user)
            self.capabilities = caps
            task_context = None
            if self.state.get("active_task_id"):
                from app.api.routes.assistant import task_view

                task = await tasks.owned_task(session, self.owner, self.state["active_task_id"])
                self.active = await task_view(session, task)
                task_context = {
                    k: self.active.get(k)
                    for k in (
                        "task_id",
                        "instruction",
                        "state",
                        "question",
                        "error_code",
                        "resolved_inputs",
                    )
                }
                if task.final_artifact_id:
                    self.artifact = await draft_review.owned_artifact(
                        session, self.owner, task.final_artifact_id
                    )
                    task_context["current_artifact"] = {
                        "kind": self.artifact.payload["kind"],
                        "revision": self.artifact.revision,
                        "content": self.artifact.payload["content"],
                        "note": "Existing generated or edited artifact; not fresh source evidence.",
                    }
            elif self.state.get("proposal_id"):
                proposal = await coordinator.owned(session, self.owner, self.state["proposal_id"])
                task_context = {
                    "kind": "proposal",
                    "proposal_id": proposal.id,
                    "proposal": await command_plans.view(session, proposal),
                }
        from app.calendar import event_choices

        return {
            **clock_context(self.mail_anchor, self.request.timezone),
            # Keep UI history, but never pass provider-authored agenda details as
            # instructions or remembered facts to the next model decision.
            "recent_dialogue": model_history(self.state["history"]),
            "previous_calendar_request": calendar_context.model_context(self.state),
            "remembered_email_sources": mail_context.model_context(self.state),
            "pending_calendar_event": event_choices.model_context(self.state),
            "history_limit": 12,
            "user_turn": self.request.instruction,
            "current_user_goal": (
                self.goal_instruction
                if self.goal_instruction != self.request.instruction.strip()
                else None
            ),
            "user_recipient_refs": self.recipients,
            "selected_reference": "selected" if "selected" in self.state["refs"] else None,
            "selected_source_scopes": (
                ["selected_message", "visible_thread", "thread"]
                if self.state["refs"].get("selected", {}).get("message_id")
                else ["visible_thread", "thread"]
                if "selected" in self.state["refs"]
                else []
            ),
            "displayed_result_order": self.state.get("result_order", []),
            "active_work": task_context,
            "capabilities": caps,
            "source_policy": (
                "No imported mailbox. Read references on demand. Pinned source stays "
                "until user changes it."
            ),
        }

    async def call(self, name, args):
        if name == "list_calendars":
            from app.calendar import event_choices

            if event_choices.pending(self.state):
                return await event_choices.show(self)
        if name == "prepare_calendar_event":
            from app.calendar.conversation_guard import (
                CalendarNewGoalRequired,
                creation_turn,
                social_response,
            )
            from app.calendar.event_creation import prepare

            if acknowledgment := social_response(self.request.instruction):
                return acknowledgment
            if args.continue_previous and creation_turn(self.request.instruction):
                raise CalendarNewGoalRequired
            return await prepare(self, args)
        if name == "retry_calendar_read":
            return await calendar_context.retry(self)
        if name in calendar_context.WINDOW_TOOLS:
            return await calendar_context.read(self, name, args)
        if name in CALENDAR_READ_TOOLS:
            from app.calendar.conversation_tools import execute

            return await execute(
                self.owner,
                name,
                args,
                self.authoritative_instruction(),
                anchor=self.calendar_anchor,
            )
        if name in {"search_mail", "more_mail"}:
            return await self.search(args if name == "search_mail" else None)
        if name == "read_email":
            return await self.read(args.reference, args.scope)
        if name == "read_search_results":
            return await self.read_search_results(args.references)
        if name == "read_calendar":
            return await self.read_calendar(args.period)
        if name == "prepare_workflow":
            return await self.workflow(args)
        if name == "answer_question":
            return await self.answer(args)
        if name == "revise_draft":
            return await self.revise(args)
        raise ValueError("Unknown capability")

    async def read_calendar(self, period):
        from app.calendar import agenda

        validate_agenda_request(self.authoritative_instruction(), period)
        result = await agenda.read(self.owner, period)
        return {
            "kind": "message",
            "text": agenda.render(result),
            "agenda": result.model_dump(mode="json"),
        }

    async def search(self, args):
        if args is not None and self.fresh_search_scope:
            self.fresh_search_attempted = True
        if get_settings().gmail_source_mode != "on_demand":
            raise ApiError(409, "live_inbox_required", "Connect live Gmail to search.")
        cursor = None
        if args is not None:
            query, sender_email, folder, date_phrase, limit = (
                args.query,
                args.sender_email,
                args.folder,
                args.date_phrase,
                1 if args.selection == "latest_message" else args.limit,
            )
            if self.fresh_search_scope:
                if self.fresh_search_scope["kind"] == "recent_inbox":
                    # The latest turn explicitly requests an unfiltered Inbox
                    # listing; an old merchant query cannot narrow it.
                    query, sender_email, folder, date_phrase = "", "", "INBOX", ""
                    limit = self.fresh_search_scope["limit"]
                else:
                    # `from:` is produced by the backend, never copied from a
                    # model-generated Gmail operator or an earlier search.
                    sender_email = self.fresh_search_scope["sender_email"]
                    if query.casefold() == sender_email.casefold():
                        query = ""
                    elif query and query.casefold() not in self.request.instruction.casefold():
                        query = ""
                    if query.casefold() in {"email", "emails", "message", "messages"}:
                        query = ""
                    if (
                        date_phrase
                        and date_phrase.casefold() not in self.request.instruction.casefold()
                    ):
                        date_phrase = ""
                    folder = (
                        "INBOX"
                        if inbox_chat.folder_is_grounded("INBOX", self.request.instruction)
                        else "all_mail"
                    )
            user_text = "\n".join(
                [h["user"] for h in self.state["history"]] + [self.request.instruction]
            )
            for value in (query, date_phrase, sender_email):
                if value and value.casefold() not in user_text.casefold():
                    raise ValueError("Search literals must come from user dialogue")
            if not inbox_chat.folder_is_grounded(folder, user_text):
                raise ValueError("Folder is not user supplied")
            start, end = inbox_chat.date_window(
                date_phrase, self.mail_anchor, self.request.timezone
            )
            filters = InboxFilters(
                schema_version="1.0",
                query=query,
                sender_email=sender_email,
                folder=folder,
                received_from=start,
                received_before=end,
                limit=limit,
                timezone=self.request.timezone,
                inbox_category=args.inbox_category if folder == "INBOX" else "all",
            )
        else:
            if self.fresh_search_scope and not self.fresh_search_done:
                raise ValueError("Start a new search for this request before paging")
            previous = self.state.get("search")
            if not previous or not previous.get("next_cursor"):
                raise ApiError(409, "search_exhausted", "There are no more results in this search.")
            if len(self.state["result_order"]) >= SEARCH_REFERENCE_LIMIT:
                raise ApiError(422, "search_limit", "Narrow this search with a sender or date.")
            filters = InboxFilters.model_validate_json(json.dumps(previous["filters"]))
            cursor = previous["next_cursor"]
        page = await inbox_chat.search(self.owner, filters, cursor)
        if args is not None and self.fresh_search_scope:
            self.fresh_search_done = True
        if args is not None:
            mail_context.reset_search(self.state)
            for mapping in (self.evidence, self.loaded, self.read_scopes, self.last_read_scopes):
                for key in list(mapping):
                    if key not in self.state["refs"]:
                        mapping.pop(key)
            self.source_selections = {
                key: value
                for key, value in self.source_selections.items()
                if key[0] in self.state["refs"]
            }
        observations = []
        retained_results = []
        for row in page["results"]:
            row.update(
                received_display(
                    row.get("received_at"), filters.timezone, reference_at=self.mail_anchor
                )
            )
            ref = next(
                (
                    key
                    for key, v in self.state["refs"].items()
                    if key in self.state["result_order"]
                    and v.get("message_id") == row["message_id"]
                ),
                None,
            )
            if ref is None:
                if len(self.state["result_order"]) >= SEARCH_REFERENCE_LIMIT:
                    continue
                ref = f"mail-{len(self.state['result_order']) + 1}"
                self.state["refs"][ref] = {
                    "message_id": row["message_id"],
                    "thread_id": row["thread_id"],
                }
                self.state["result_order"].append(ref)
            row["reference"] = ref
            retained_results.append(row)
            observations.append(
                {k: v for k, v in row.items() if k not in {"message_id", "thread_id"}}
            )
        page["results"] = retained_results
        if len(self.state["result_order"]) >= SEARCH_REFERENCE_LIMIT:
            page["next_cursor"] = None
        self.state["search"] = {k: page[k] for k in ("filters", "next_cursor", "coverage")}
        # The model sees one page at a time, but the UI needs every card shown
        # during this turn. Otherwise a second page can leave an earlier
        # `mail-N` addressable even though its card was never returned to the UI.
        # A new search resets the aggregate because its references are replaced.
        previous_cards = self.search_page["results"] if args is None and self.search_page else []
        cards_by_reference = {row["reference"]: row for row in [*previous_cards, *retained_results]}
        visible_cards = [
            cards_by_reference[ref]
            for ref in self.state["result_order"]
            if ref in cards_by_reference
        ]
        self.search_page = {**page, "results": visible_cards}
        # Return only this page to the model; IDs/order survive, original
        # subject/body/snippets do not persist in conversation storage.
        return {
            "results": observations,
            "has_more": bool(page["next_cursor"]),
            "coverage": page["coverage"],
            "date_window": page["filters"],
            "displayed_result_order": self.state["result_order"],
        }

    async def read(self, reference, scope="selected_message"):
        ref = self.state["refs"].get(reference)
        if not ref:
            raise ValueError("Unknown source reference")
        if scope == "visible_thread" and not ref.get("context_id"):
            raise ValueError("Visible thread requires an owned pinned capture")
        source = await source_data.fetch(self.owner, ref["thread_id"])
        captured_messages = None
        if ref.get("context_id"):
            async with self.factory() as session:
                context = await session.scalar(
                    select(ContextSnapshot).where(
                        ContextSnapshot.id == ref["context_id"],
                        ContextSnapshot.user_id == self.owner,
                    )
                )
                if not context:
                    raise ApiError(404, "context_not_found", "Select that email again.")
                payload = source_data.context_data(context)
                message_id = ref.get("message_id")
                available = {m["message_id"] for m in payload["messages"]}
                if scope == "thread":
                    mids = {m["gmail_msg_id"] for m in source["messages"]}
                elif scope == "visible_thread":
                    mids = available
                    captured_messages = payload["messages"]
                elif message_id:
                    if message_id not in available:
                        raise ApiError(
                            404, "ui_reference_not_found", "Select one accessible email."
                        )
                    mids = {message_id}
                elif "ui_map" not in payload:
                    mids = available  # Legacy full-thread capture has no selected message.
                    scope = "visible_thread"
                    captured_messages = payload["messages"]
                else:
                    raise ApiError(404, "ui_reference_not_found", "Select one accessible email.")
        else:
            mids = (
                {m["gmail_msg_id"] for m in source["messages"]}
                if scope == "thread"
                else {ref["message_id"]}
            )
        if scope == "thread":
            from app.assistant.context_plan import MAX_MESSAGES, sample

            messages_in_scope = [m for m in source["messages"] if m["gmail_msg_id"] in mids]
            mids = {
                m["gmail_msg_id"]
                for m in sample(messages_in_scope, MAX_MESSAGES, ref.get("message_id"))
            }
        messages = [m for m in source["messages"] if m["gmail_msg_id"] in mids]
        if not messages or not mids.issubset({m["gmail_msg_id"] for m in messages}):
            raise ApiError(404, "gmail_source_missing", "That email is no longer available.")
        if captured_messages is not None:
            by_id = {m["gmail_msg_id"]: m for m in messages}
            messages = [by_id[m["message_id"]] for m in captured_messages]
            captured_bodies = {m["message_id"]: m["body"] for m in captured_messages}
        else:
            captured_bodies = {}
        budget = 12000 // len(messages)
        output = [
            {
                "subject": m["subject"],
                "sender": m["from_addr"],
                "sent_at": m["sent_at"],
                "received_at": m["received_at"],
                **received_display(
                    m["received_at"], self.request.timezone, reference_at=self.mail_anchor
                ),
                "reply_to": m["reply_metadata"].get("headers", {}).get("reply-to", []),
                "body": captured_bodies.get(m["gmail_msg_id"], m["body_clean"][:budget]),
                "truncated": len(m["body_clean"])
                > len(captured_bodies.get(m["gmail_msg_id"], m["body_clean"][:budget])),
            }
            for m in messages
        ]
        self.evidence[reference] = "\n".join(
            str(m.get(k) or "") for m in output for k in ("subject", "sender", "reply_to", "body")
        )
        self.loaded[reference] = source
        self.read_scopes.setdefault(reference, set()).add(scope)
        self.last_read_scopes[reference] = scope
        self.source_selections[(reference, scope)] = {
            "thread_id": ref["thread_id"],
            "scope": scope,
            "message_ids": None if scope == "thread" else [m["gmail_msg_id"] for m in messages],
        }
        remembered = mail_context.retain(self.state, reference, scope)
        if remembered not in self.turn_source_references:
            self.turn_source_references.append(remembered)
        return {
            "reference": reference,
            "remembered_reference": remembered,
            "scope": scope,
            "messages": output,
            "untrusted_source": True,
            "coverage": (
                "provider thread messages"
                if scope == "thread"
                else "captured thread messages"
                if scope == "visible_thread"
                else "selected message only"
            ),
            "total_thread_messages": len(source["messages"]),
            "included_messages": len(output),
            "omitted_messages": len(source["messages"]) - len(output),
            "truncated_messages": sum(m["truncated"] for m in output),
            "attachments": "not_read",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def read_search_results(self, references):
        """Inspect one bounded batch of currently displayed Gmail search references.

        Reuse the single-message read path for owner checks and source identity.
        Only the capped excerpts enter the model transcript and citation evidence.
        """
        if not 1 <= len(references) <= 5 or len(set(references)) != len(references):
            raise ValueError("Choose one to five distinct search references")
        displayed = set(self.state.get("result_order", []))
        for reference in references:
            source = self.state["refs"].get(reference)
            if (
                not re.fullmatch(r"mail-[1-9][0-9]*", reference)
                or reference not in displayed
                or not source
                or not source.get("message_id")
                or not source.get("thread_id")
                or source.get("context_id")
            ):
                raise ValueError("Use only current searched email references")

        from copy import deepcopy

        memory_before = deepcopy(self.state)
        selections_before = self.source_selections.copy()
        last_scopes_before = self.last_read_scopes.copy()
        turn_refs_before = self.turn_source_references.copy()
        sentinel = object()
        prior = {
            reference: (
                self.evidence.get(reference, sentinel),
                self.loaded.get(reference, sentinel),
                set(self.read_scopes[reference]) if reference in self.read_scopes else sentinel,
            )
            for reference in references
        }
        results = []
        try:
            for reference in references:
                result = await self.read(reference)
                messages = []
                for message in result["messages"]:
                    body = message["body"]
                    messages.append(
                        {
                            **message,
                            "body": body[:SEARCH_READ_BODY_CHARS],
                            "truncated": message["truncated"] or len(body) > SEARCH_READ_BODY_CHARS,
                        }
                    )
                excerpt_evidence = "\n".join(
                    str(message.get(field) or "")
                    for message in messages
                    for field in ("subject", "sender", "reply_to", "body")
                )
                earlier_evidence = prior[reference][0]
                # An earlier full read was also shown to the model. A later
                # capped batch must not invalidate a quote from that read.
                self.evidence[reference] = (
                    excerpt_evidence
                    if earlier_evidence is sentinel
                    else earlier_evidence + "\n" + excerpt_evidence
                )
                results.append({"reference": reference, "messages": messages})
        except Exception:
            self.state.clear()
            self.state.update(memory_before)
            self.source_selections = selections_before
            self.last_read_scopes = last_scopes_before
            self.turn_source_references = turn_refs_before
            for reference, values in prior.items():
                for mapping, value in zip(
                    (self.evidence, self.loaded, self.read_scopes), values, strict=True
                ):
                    if value is sentinel:
                        mapping.pop(reference, None)
                    else:
                        mapping[reference] = value
            raise

        return {
            "results": results,
            "untrusted_source": True,
            "coverage": "selected searched messages only",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def capture(self, reference, *, scope="selected_message", supporting=()):
        if reference is None:
            return None, None
        if reference not in self.loaded:
            raise ValueError("Read this email before preparing work")
        ref = self.state["refs"].get(reference)
        if ref is None:
            raise ValueError("This source handle expired; select or search for it again")
        if scope == "thread" or supporting:
            from app.assistant import context_plan

            selections = [self.source_selections[(reference, scope)]]
            for key in supporting:
                supporting_scope = self.last_read_scopes.get(key)
                if supporting_scope is None:
                    raise ValueError("Read every supporting source before preparing work")
                selections.append(self.source_selections[(key, supporting_scope)])
            async with self.factory.begin() as session:
                context = await context_plan.capture(
                    session, self.owner, selections, ref.get("message_id")
                )
            return context.id, ref.get("message_id")
        if scope == "visible_thread":
            if not ref.get("context_id"):
                raise ValueError("Visible thread requires an owned pinned capture")
            return ref["context_id"], None
        message_id = ref.get("message_id")
        if not message_id:
            if ref.get("context_id") and "visible_thread" in self.read_scopes.get(reference, set()):
                return ref["context_id"], None
            raise ValueError("Select one email before preparing work")
        async with self.factory.begin() as session:
            context = await source_data.capture(
                session,
                self.owner,
                ref["thread_id"],
                message_id=message_id,
            )
        return context.id, message_id

    async def workflow(self, args):
        instruction = self.authoritative_instruction()
        authorize_workflow(instruction, args.intent, args.compound)
        validate_workflow_bindings(
            args, set(self.loaded), set(self.recipients), self.recipient_roles
        )
        scope = args.source_scope
        ref = self.state["refs"].get(args.reference, {})
        if scope == "visible_thread" and not ref.get("context_id"):
            raise ValueError("Visible thread requires an owned pinned reference")
        if scope == "selected_message" and not ref.get("message_id"):
            if ref.get("context_id") and "visible_thread" in self.read_scopes.get(
                args.reference, set()
            ):
                scope = "visible_thread"
        if args.reference and scope not in self.read_scopes.get(args.reference, set()):
            raise ValueError("Read the requested source scope before preparing work")
        if any(key not in self.loaded for key in args.context_references):
            raise ValueError("Read every supporting source before preparing work")
        context_id, mid = await self.capture(
            args.reference, scope=scope, supporting=args.context_references
        )
        provenance = self.conversation_provenance(instruction)
        if args.reference:
            provenance["source_scope"] = scope
            provenance["context_references"] = args.context_references
        draft = DraftOptions(reply_message_id=mid) if args.intent == "reply" and mid else None
        roles = {role: getattr(args, role + "_refs") for role in ("to", "cc", "bcc")}
        if any(roles.values()):
            draft = DraftOptions(
                reply_message_id=mid if args.intent == "reply" else None,
                **{role: [self.recipients[ref] for ref in refs] for role, refs in roles.items()},
            )
        from app.api.routes.assistant import task_view

        key = "chat-" + self.request.request_id
        if args.intent == "plan_schedule" or args.compound:
            async with self.factory() as session:
                prefs = await session.scalar(
                    select(CalendarPreference).where(CalendarPreference.user_id == self.owner)
                )
                request = CoordinatorRequest(
                    schema_version="1.0",
                    request_id=key,
                    instruction=instruction,
                    context_snapshot_id=context_id,
                    draft_options=draft,
                    expected_preferences_version=prefs.version if prefs else None,
                )
                row, created = await coordinator.reserve(
                    session, self.owner, request, provenance=provenance
                )
                self.state["proposal_id"] = row.id
                self.state.pop("active_task_id", None)
                await self.checkpoint(
                    session,
                    {
                        "kind": "proposal",
                        "text": proposal_text("planning"),
                        "proposal_id": row.id,
                    },
                )
                await session.commit()
                if created or row.state == "planning":
                    state, value = await coordinator.interpret(row)
                    row = await command_plans.complete(session, self.owner, row.id, state, value)
                proposal = await command_plans.view(session, row)
                await session.commit()
            self.state["proposal_id"] = row.id
            return {
                "kind": "proposal",
                "text": proposal_text(proposal["state"]),
                "proposal": proposal,
                "proposal_id": row.id,
            }
        operation = (
            "summary"
            if args.intent == "summarise"
            else "draft_new"
            if args.intent == "compose" and context_id and draft and draft.to
            else None
        )
        workflow = (
            WorkflowRequest(
                schema_version="1.0",
                request_id=key,
                instruction=instruction,
                context_snapshot_id=context_id,
                operations=[operation],
                draft_options=draft if operation == "draft_new" else None,
            )
            if operation
            else None
        )
        request = (
            workflow.as_request()
            if workflow
            else AssistantRequest(
                schema_version="1.0",
                request_id=key,
                instruction=instruction,
                intent_hint=args.intent,
                context_snapshot_id=context_id,
                continuation=None,
                draft_options=draft,
            )
        )
        async with self.factory.begin() as session:
            task = await tasks.submit(
                session, self.owner, request, workflow=workflow, provenance=provenance
            )
            view = await task_view(session, task)
            self.state["active_task_id"] = task.id
            self.state.pop("proposal_id", None)
            await self.checkpoint(
                session, {"kind": "task", "text": "I’m preparing that for you.", "task_id": task.id}
            )
        self.state["active_task_id"] = task.id
        return {
            "kind": "task",
            "text": "I’m preparing that for you.",
            "task_id": task.id,
            "task": view,
        }

    async def answer(self, args):
        if (
            not self.active
            or self.active["state"] != "needs_clarification"
            or not self.active.get("question")
        ):
            raise ValueError("No active typed question")
        from app.api.routes.assistant import task_view

        request = TaskInputRequest(
            schema_version="1.0",
            request_id="chat-" + self.request.request_id,
            expected_version=self.active["version"],
            question_id=self.active["question"]["question_id"],
            answer=args.answer,
        )
        validate_clarification(
            args.answer,
            self.request.instruction,
            self.active["question"],
            self.request.timezone,
        )
        async with self.factory.begin() as session:
            task = await continuation.accept_input(
                session, self.owner, self.active["task_id"], request
            )
            view = await task_view(session, task)
            await self.checkpoint(
                session, {"kind": "task", "text": "Thanks, I’ll use that.", "task_id": task.id}
            )
        return {"kind": "task", "text": "Thanks, I’ll use that.", "task_id": task.id, "task": view}

    async def revise(self, args):
        if not self.artifact or self.artifact.payload["kind"] != "draft":
            raise ValueError("No current draft")
        envelope = self.artifact.draft_envelope
        if not envelope:
            raise ValueError("No draft envelope")
        context_id = self.artifact.payload.get("context_snapshot_id")
        if context_id:
            async with self.factory() as session:
                context = await session.scalar(
                    select(ContextSnapshot).where(
                        ContextSnapshot.id == context_id,
                        ContextSnapshot.user_id == self.owner,
                    )
                )
                if context is None:
                    raise ApiError(404, "context_not_found", "Select the evidence again.")
                reference = context.payload
            await source_data.prefetch(self.owner, [reference])
        # Fetch original source for freshness without changing the pinned UI selection.
        reply = envelope.get("reply")
        if reply:
            await source_data.fetch(self.owner, reply["gmail_thread_id"])
        request = EditDraftRequest(
            request_id="chat-" + self.request.request_id,
            expected_revision=self.artifact.revision,
            subject=args.subject,
            body=args.body,
            recipients=DraftRecipients(to=envelope["to"], cc=envelope["cc"], bcc=envelope["bcc"]),
            unresolved_fields=self.artifact.payload["content"]["unresolved_fields"],
        )
        from app.api.routes.assistant import task_view

        async with self.factory.begin() as session:
            task, artifact = await draft_review.edit(
                session,
                self.owner,
                self.artifact.task_id,
                request,
                author="conversation_model",
                author_provenance=self.conversation_provenance(self.authoritative_instruction()),
            )
            await session.refresh(task)
            view = await task_view(session, task)
            result = await draft_review.artifact_view(session, task, artifact)
            await self.checkpoint(
                session,
                {
                    "kind": "task",
                    "text": "Here is the revised draft. Please review it before using it.",
                    "task_id": task.id,
                },
            )
        return {
            "kind": "task",
            "text": "Here is the revised draft. Please review it before using it.",
            "task_id": task.id,
            "task": view,
            "artifacts": [result],
        }

    async def checkpoint(self, session, response):
        if self.lease is not None:
            from app.conversation.store import checkpoint

            await checkpoint(
                session,
                self.owner,
                self.request,
                self.lease,
                self.state,
                {
                    **response,
                    "context_references": self.turn_source_references,
                },
            )
