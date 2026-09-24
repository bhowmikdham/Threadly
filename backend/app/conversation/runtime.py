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
from app.db.models import CalendarPreference, ContextSnapshot, User
from app.schemas.assistant import AssistantRequest, DraftOptions
from app.schemas.continuation import TaskInputRequest
from app.schemas.coordinator import CoordinatorRequest
from app.schemas.draft_review import DraftRecipients, EditDraftRequest
from app.schemas.inbox_chat import InboxFilters
from app.schemas.workflow import WorkflowRequest

SEARCH_REFERENCE_LIMIT = 25
SEARCH_READ_BODY_CHARS = 2000
WORKFLOW_BINDING_ERROR = "workflow_binding_invalid"


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


def user_recipient_references(user_text):
    """Issue recipient handles only for addresses written by the user."""

    addresses = list(
        dict.fromkeys(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_text))
    )[:20]
    return {f"recipient-{i + 1}": address for i, address in enumerate(addresses)}


def validate_workflow_bindings(args, loaded_references, recipient_references):
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


def authoritative_user_instruction(latest_turn, history):
    """Resolve bounded follow-ups using user-authored dialogue only."""

    latest = latest_turn.strip()
    previous = [entry.get("user", "").strip() for entry in history]
    previous = [value for value in previous if value]
    if previous and _is_contextual_followup(latest):
        return previous[-1] + "\nUser follow-up: " + latest
    return latest


def authorize_workflow(instruction, intent, compound):
    """Require a user-authored request for every model-selected workflow class."""
    value = _normalise_words(instruction)
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
        r"\b(?:give|find)\s+(?:me\s+)?(?:(?:one|two|three|\d+)\s+)?"
        r"(?:slots?|times? to meet)\b|"
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


class Runtime:
    def __init__(self, owner, request, state, factory, lease=None):
        self.owner, self.request, self.state, self.factory = owner, request, state, factory
        self.lease = lease
        self.evidence, self.loaded, self.read_scopes = {}, {}, {}
        self.search_page = None
        self.fresh_search_scope = fresh_search_scope(request.instruction)
        self.fresh_search_done = False
        self.fresh_search_attempted = False
        self.active = self.artifact = None
        # Addresses are exposed as backend-issued references, not writable model strings.
        user_text = "\n".join([h["user"] for h in state["history"]] + [request.instruction])
        self.recipients = user_recipient_references(user_text)

    def _reset_previous_search(self):
        # A new explicit mailbox request supersedes old search references. The
        # independently pinned source, if any, remains available to the user.
        self.state["refs"] = {
            key: value for key, value in self.state["refs"].items() if key == "selected"
        }
        self.state["result_order"] = []
        self.state.pop("search", None)

    def authoritative_instruction(self):
        """Build workflow input exclusively from bounded user-authored turns.

        Model-authored tool arguments and fetched email bodies are deliberately
        excluded. A short deictic follow-up receives only the immediately previous
        user turn so downstream planners can resolve "draft that" without granting
        a source email authority over the request.
        """
        return authoritative_user_instruction(self.request.instruction, self.state["history"])

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
                    if ui_map is None or len(selected) == 1:
                        self.state["refs"]["selected"] = {
                            "message_id": selected[0] if selected else None,
                            "context_id": context.id,
                            "thread_id": context.payload["thread_id"],
                        }
                    else:
                        self.state["refs"].pop("selected", None)
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
        return {
            "now": datetime.now(UTC).isoformat(),
            "timezone": self.request.timezone,
            "recent_dialogue": self.state["history"],
            "history_limit": 12,
            "user_turn": self.request.instruction,
            "user_recipient_refs": self.recipients,
            "selected_reference": "selected" if "selected" in self.state["refs"] else None,
            "selected_source_scopes": (
                ["selected_message", "visible_thread"]
                if self.state["refs"].get("selected", {}).get("message_id")
                else ["visible_thread"]
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
        if name in {"search_mail", "more_mail"}:
            return await self.search(args if name == "search_mail" else None)
        if name == "read_email":
            return await self.read(args.reference, args.scope)
        if name == "read_search_results":
            return await self.read_search_results(args.references)
        if name == "prepare_workflow":
            return await self.workflow(args)
        if name == "answer_question":
            return await self.answer(args)
        if name == "revise_draft":
            return await self.revise(args)
        raise ValueError("Unknown capability")

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
                args.limit,
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
                        if re.search(r"\binbox\b", self.request.instruction, re.I)
                        else "all_mail"
                    )
            user_text = "\n".join(
                [h["user"] for h in self.state["history"]] + [self.request.instruction]
            )
            for value in (query, date_phrase, sender_email):
                if value and value.casefold() not in user_text.casefold():
                    raise ValueError("Search literals must come from user dialogue")
            if folder != "all_mail" and folder.casefold() not in user_text.casefold():
                raise ValueError("Folder is not user supplied")
            start, end = inbox_chat.date_window(
                date_phrase, datetime.now(UTC), self.request.timezone
            )
            filters = InboxFilters(
                schema_version="1.0",
                query=query,
                sender_email=sender_email,
                folder=folder,
                received_from=start,
                received_before=end,
                limit=limit,
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
            self.evidence = {k: v for k, v in self.evidence.items() if k == "selected"}
            self.loaded = {k: v for k, v in self.loaded.items() if k == "selected"}
            self.read_scopes = {k: v for k, v in self.read_scopes.items() if k == "selected"}
            self.state["refs"] = {k: v for k, v in self.state["refs"].items() if k == "selected"}
            self.state["result_order"] = []
        observations = []
        retained_results = []
        for row in page["results"]:
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
        if scope == "visible_thread" and (reference != "selected" or not ref.get("context_id")):
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
                if scope == "visible_thread":
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
            mids = {ref["message_id"]}
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
        return {
            "reference": reference,
            "messages": output,
            "untrusted_source": True,
            "coverage": (
                "captured thread messages" if scope == "visible_thread" else "selected message only"
            ),
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

    async def capture(self, reference, *, scope="selected_message"):
        if reference is None:
            return None, None
        if reference not in self.loaded:
            raise ValueError("Read this email before preparing work")
        ref = self.state["refs"][reference]
        if scope == "visible_thread":
            if reference != "selected" or not ref.get("context_id"):
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
        validate_workflow_bindings(args, set(self.loaded), set(self.recipients))
        scope = args.source_scope
        if scope == "visible_thread" and args.reference != "selected":
            raise ValueError("Visible thread requires an owned pinned reference")
        ref = self.state["refs"].get(args.reference, {})
        if scope == "selected_message" and not ref.get("message_id"):
            if ref.get("context_id") and "visible_thread" in self.read_scopes.get(
                args.reference, set()
            ):
                scope = "visible_thread"
        if args.reference and scope not in self.read_scopes.get(args.reference, set()):
            raise ValueError("Read the requested source scope before preparing work")
        context_id, mid = await self.capture(args.reference, scope=scope)
        provenance = self.conversation_provenance(instruction)
        if args.reference:
            provenance["source_scope"] = scope
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
                        "text": "Here is the proposed work for you to review.",
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
                "text": "Here is the proposed work for you to review.",
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

            await checkpoint(session, self.owner, self.request, self.lease, self.state, response)
