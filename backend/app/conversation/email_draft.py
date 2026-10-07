"""Collect a user-authored compose goal before reserving any email workflow.

Named recipients are sufficient for text. Only the existing envelope-bound workflow
can produce an actionable artifact; conversation text never grants send authority.
"""

import re

from app.api.errors import ApiError
from app.schemas.assistant import DraftOptions, RouteDecision, RouteParameters
from app.schemas.conversation import PrepareWorkflow

KEY = "email_draft_goal"


class EmailDraftRequired(ValueError):
    """Repair a premature workflow call through the compose clarification tool."""


class EmailDraftInputError(ValueError):
    """Allowlisted repair reason; never contains user text."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(
            {
                "user_field_required": "Copy fields from USER text",
                "source_bound_workflow_required": "Use the source-bound workflow for read email",
            }.get(reason, reason)
        )


def current_text_draft(state):
    goal = state.get(KEY) or {}
    if goal.get("status") != "drafted" or state.get("active_task_id") or state.get("proposal_id"):
        return None
    for entry in reversed(state.get("history", [])):
        draft = entry.get("email_draft")
        if draft and (not goal.get("draft_id") or draft["draft_id"] == goal["draft_id"]):
            return draft
    if goal.get("draft_id") and goal.get("draft"):
        return {
            "draft_id": goal["draft_id"],
            "recipient": goal["recipient"],
            **{key: goal["draft"][key] for key in ("subject", "body", "unresolved_fields")},
        }
    return None


def active_text_id(state):
    draft = current_text_draft(state)
    return draft["draft_id"] if draft else None


def model_context(state):
    value = state.get(KEY)
    # Address-bound drafts now belong to active_work and its immutable artifact
    # revision flow. Do not invite the model to regenerate them from old turns.
    if (
        not value
        or value.get("status") == "workflow"
        or (
            value.get("status") == "drafted"
            and (state.get("active_task_id") or state.get("proposal_id"))
        )
    ):
        return None
    result = dict(value)
    if result.get("status") == "drafted" and "draft" not in result:
        latest = current_text_draft(state)
        if latest:
            result["draft"] = {k: latest[k] for k in ("subject", "body", "unresolved_fields")}
            result["draft"]["sources"] = []
    return result


def ready_route():
    """The validated user-only compose goal needs no second intent classification."""
    from app.assistant.routing import RELEASE

    return {
        "decision": RouteDecision(
            schema_version="1.0",
            status="ready",
            intent="compose",
            output_kind="draft",
            operations=["draft_new"],
            context_snapshot_id=None,
            parameters=RouteParameters.empty(),
            missing_fields=[],
            clarification=None,
            rationale="User supplied the email draft's recipient and purpose.",
            requested_action="none",
        ).model_dump(),
        "source": "rule",
        "router_version": "email-draft-preflight-1.0.0",
        "provenance": None,
        "release": RELEASE,
    }


def is_compose(instruction):
    from app.conversation.runtime import authorize_workflow

    try:
        authorize_workflow(instruction, "compose", False)
    except ValueError:
        return False
    return True


def requires_preparation(runtime):
    # Source-based and compound workflows retain their existing read/binding path.
    # Questions about an existing artifact and social turns are not new requests.
    if getattr(runtime, "loaded", None):
        return False
    latest = runtime.request.instruction
    if is_compose(latest) and not re.match(r"\s*(?:should|would)\s+(?:i|we)\b", latest, re.I):
        return True
    from app.conversation.runtime import _revokes_compose_request, _starts_independent_request

    goal = runtime.state.get(KEY) or {}
    if goal.get("status") != "clarification" or _revokes_compose_request(latest):
        return False
    if getattr(runtime, "email_draft_attempted", False):
        return True
    # Short answers to the retained question must establish structured state.
    # Independent questions and social detours may still use ordinary responses.
    return not (
        _starts_independent_request(latest)
        or re.fullmatch(r"\s*(?:hi|hello|hey|thanks|thank you|ok|okay)[.! ]*", latest, re.I)
        or re.match(r"\s*(?:what|why|how|can|could|would|is|are|do|does|am)\b", latest, re.I)
    )


def _quoted(value, instruction):
    return (
        not value or " ".join(value.split()).casefold() in " ".join(instruction.split()).casefold()
    )


async def prepare(runtime, args):
    from app.conversation import citations, goals
    from app.conversation.runtime import (
        _revokes_compose_request,
        user_recipient_roles,
    )

    latest = runtime.request.instruction.strip()
    cited = await citations.resolve(runtime, args.citations)
    user_context = list((await citations.resolve(runtime, args.context_citations)).values())
    previous = runtime.state.get(KEY)
    cancelling = _revokes_compose_request(latest)
    if args.request_source:
        from app.conversation.user_intent import validate

        if cancelling:
            # Declining creation is valid cancellation intent. Still bind it to
            # this USER turn; the creation-intent validator rejects cancellations.
            if args.request_source != runtime.request.instruction:
                raise ValueError("Copy the complete current USER turn as request_source")
        else:
            validate(args.request_source, runtime.request.instruction)
    if args.continue_previous or cancelling:
        previous = await goals.bind_email_continuation(runtime, args)
    if cancelling:
        goals.close(runtime.state, previous)
        runtime.state.pop(KEY, None)
        return {"kind": "message", "text": "Okay, I won’t continue that draft."}
    if runtime.loaded:
        raise EmailDraftInputError("source_bound_workflow_required")
    if args.continue_previous:
        if not previous:
            raise EmailDraftInputError("no_pending_draft")
        if previous["status"] == "workflow":
            raise ApiError(
                422,
                "draft_artifact_required",
                "Use active_work and revise_draft for the existing saved draft. "
                "A new email goal uses continue_previous=false.",
            )
        if re.search(
            r"\b(?:new|another|different)\s+(?:email|e-mail|message|draft)\b", latest, re.I
        ):
            raise ValueError("Start the new email with continue_previous=false")
        if previous["status"] != "clarification" and is_compose(latest):
            raise ValueError("A new compose request must not reuse the completed draft's fields")
        instruction = previous["instruction"]
        if latest not in instruction.split("\nUser follow-up: "):
            instruction += "\nUser follow-up: " + latest
        values = {field: previous[field] for field in ("recipient", "purpose")}
        # Repeating retained USER fields in a full tool object is harmless. Also
        # allow recovery of an earlier user answer after a rejected draft call;
        # assistant prose is never a source of recipient/purpose authority.
        history = runtime.state.get("history", [])
        start = next(
            (
                i
                for i, h in enumerate(history)
                if (
                    previous.get("origin_request_id")
                    and h.get("request_id") == previous["origin_request_id"]
                )
                or h.get("user") == previous["instruction"].split("\nUser follow-up: ")[0]
            ),
            None,
        )
        source = (
            "\n".join([instruction] + [h.get("user", "") for h in history[start:]])
            if start is not None
            else instruction
        )
    else:
        instruction = runtime.authoritative_instruction()
        if not args.request_source and not is_compose(instruction):
            raise EmailDraftInputError("continuation_required" if previous else "compose_required")
        values = {"recipient": "", "purpose": ""}
        source = instruction
    if len(instruction) > 8000:
        return {"kind": "clarification", "text": "What should this email say, in a few sentences?"}
    for field in values:
        supplied = getattr(args, field).strip()
        retained = args.continue_previous and supplied == previous[field]
        field_source = latest if args.continue_previous and field == "recipient" else source
        field_source = cited.get(field, {}).get("source", field_source)
        if not retained and not _quoted(supplied, field_source):
            raise EmailDraftInputError("user_field_required")
        if supplied:
            values[field] = supplied
    recipient_instruction = (
        previous.get("recipient_instruction", previous["instruction"])
        if args.continue_previous and previous
        else instruction
    )
    if (
        args.continue_previous
        and args.recipient.strip()
        and (args.recipient.strip() != previous["recipient"])
    ):
        # An updated name cannot silently inherit the previous person's address.
        # Keep recipient authority with the USER turn that supplied that recipient.
        recipient_instruction = latest
    if "recipient" in cited:
        # Only the proposed recipient is bound, never unrelated roles or commands
        # that happen to occur in the older quote. The new goal still needs its
        # own current compose request above.
        recipient_instruction = values["recipient"]
    goal = {
        **values,
        **(
            {"goal_id": previous["goal_id"]}
            if args.continue_previous and previous.get("goal_id")
            else {}
        ),
        "instruction": instruction,
        "semantic_request": bool(args.request_source)
        or bool(args.continue_previous and previous.get("semantic_request")),
        "recipient_instruction": recipient_instruction,
        "status": "clarification",
        "field_provenance": {
            **(
                {
                    k: v
                    for k, v in (previous or {}).get("field_provenance", {}).items()
                    if not getattr(args, k).strip() or getattr(args, k).strip() == previous[k]
                }
                if args.continue_previous
                else {}
            ),
            **cited,
        },
        "user_context": user_context
        or ((previous or {}).get("user_context", []) if args.continue_previous else []),
        "origin_request_id": (
            previous.get("origin_request_id")
            if args.continue_previous
            else runtime.request.request_id
        ),
        "superseded_task_id": (
            previous.get("superseded_task_id")
            if args.continue_previous and previous
            else runtime.state.get("active_task_id")
        ),
    }
    # A literal address is bound only by the existing user-role resolver. A copied
    # name or an incidental address in message content cannot become an envelope.
    roles = user_recipient_roles(recipient_instruction, runtime.state["history"], latest)
    if recipient_instruction.strip() == values["recipient"]:
        try:
            # A literal mailbox provided as the whole answer is a recipient;
            # a name, quoted header, or address mentioned in other text is not.
            address = DraftOptions(to=[values["recipient"]]).to[0]
        except ValueError:
            pass
        else:
            roles = {"to": {address}, "cc": set(), "bcc": set()}
    if args.continue_previous and previous.get("recipient_roles"):
        # Replacing To does not silently drop a previously supplied Cc/Bcc.
        # A newly stated role replaces that role; only user-bound values persist.
        for role in ("cc", "bcc"):
            if not re.search(rf"\b{role}\b", recipient_instruction, re.I):
                roles[role] = set(previous["recipient_roles"][role])
    goal["recipient_roles"] = {role: sorted(addresses) for role, addresses in roles.items()}
    missing = [field for field, value in values.items() if not value]
    if not args.continue_previous:
        runtime.state.pop("active_task_id", None)
        runtime.state.pop("proposal_id", None)
        runtime.active = runtime.artifact = None
    if missing:
        runtime.state[KEY] = goal
        question = (
            "Who’s it for, and what would you like to say?"
            if len(missing) == 2
            else "Who’s it for?"
            if missing == ["recipient"]
            else "What would you like to say?"
        )
        return {"kind": "clarification", "text": question}

    addresses = list(
        dict.fromkeys(address for values in roles.values() for address in sorted(values))
    )
    recipients = {f"recipient-{index + 1}": address for index, address in enumerate(addresses)}
    if roles["to"]:
        runtime.goal_instruction = instruction
        runtime.recipients = recipients
        runtime.recipient_roles = {
            role: {ref for ref, address in recipients.items() if address in addresses}
            for role, addresses in roles.items()
        }
        runtime.verified_user_context = (
            list(goal["field_provenance"].values()) + goal["user_context"]
        )
        runtime.email_draft_prepared = True
        goal["status"] = "workflow"
        runtime.state[KEY] = goal
        result = await runtime.workflow(
            PrepareWorkflow(
                intent="compose",
                request_source=runtime.request.instruction if goal["semantic_request"] else "",
                **{role + "_refs": sorted(refs) for role, refs in runtime.recipient_roles.items()},
            )
        )
        goal["task_id"] = result["task_id"]
    else:
        # Preserve validated details even when generation needs a repair. A failed
        # text payload must not make the next turn ask for the message again.
        if not (args.continue_previous and previous.get("status") == "drafted"):
            runtime.state[KEY] = goal
        if args.draft is None:
            raise EmailDraftInputError("draft_text_required")
        if args.draft.sources:
            raise ValueError("A user-only text draft has no source message numbers")
        goal["status"] = "drafted"
        goal["draft"] = args.draft.model_dump()
        goal["draft_id"] = runtime.request.request_id
        result = {
            "kind": "message",
            "email_draft": {
                "draft_id": runtime.request.request_id,
                "recipient": values["recipient"],
                "subject": args.draft.subject,
                "body": args.draft.body,
                "unresolved_fields": args.draft.unresolved_fields,
            },
            "text": "Here’s a draft you can edit. Nothing has been sent.\n\n"
            + "Subject: "
            + args.draft.subject
            + "\n\n"
            + args.draft.body,
        }
        if args.draft.unresolved_fields:
            result["text"] += "\n\nTo fill in: " + "; ".join(args.draft.unresolved_fields)
    runtime.state[KEY] = goal
    return result
