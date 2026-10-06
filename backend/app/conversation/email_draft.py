"""Collect a user-authored compose goal before reserving any email workflow.

Named recipients are sufficient for text. Only the existing envelope-bound workflow
can produce an actionable artifact; conversation text never grants send authority.
"""

import re

from app.schemas.assistant import RouteDecision, RouteParameters
from app.schemas.conversation import PrepareWorkflow

KEY = "email_draft_goal"


class EmailDraftRequired(ValueError):
    """Repair a premature workflow call through the compose clarification tool."""


def model_context(state):
    value = state.get(KEY)
    return dict(value) if value else None


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
    return is_compose(latest) and not re.match(r"\s*(?:should|would)\s+(?:i|we)\b", latest, re.I)


def _quoted(value, instruction):
    return (
        not value or " ".join(value.split()).casefold() in " ".join(instruction.split()).casefold()
    )


async def prepare(runtime, args):
    from app.conversation.runtime import (
        _revokes_compose_request,
        user_recipient_references,
        user_recipient_roles,
    )

    latest = runtime.request.instruction.strip()
    previous = runtime.state.get(KEY)
    if _revokes_compose_request(latest):
        runtime.state.pop(KEY, None)
        return {"kind": "message", "text": "Okay, I won’t continue that draft."}
    if runtime.loaded:
        raise ValueError("Use the source-bound workflow for a draft based on read email")
    if args.continue_previous:
        if not previous:
            raise ValueError("No pending email draft; use the current user goal")
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
        source = latest
    else:
        instruction = runtime.authoritative_instruction()
        if not is_compose(instruction):
            raise ValueError("The user did not request email composition")
        values = {"recipient": "", "purpose": ""}
        source = instruction
    if len(instruction) > 8000:
        return {"kind": "clarification", "text": "What should this email say, in a few sentences?"}
    for field in values:
        supplied = getattr(args, field).strip()
        if not _quoted(supplied, source):
            raise ValueError(
                "Copy recipient and purpose from USER text, never source or assistant text"
            )
        if supplied:
            values[field] = supplied
    goal = {**values, "instruction": instruction, "status": "clarification"}
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

    # A literal address is bound only by the existing user-role resolver. A copied
    # name or an incidental address in message content cannot become an envelope.
    recipients = user_recipient_references(instruction, runtime.state["history"], latest)
    roles = user_recipient_roles(instruction, runtime.state["history"], latest)
    if roles["to"]:
        runtime.goal_instruction = instruction
        runtime.recipients = recipients
        runtime.recipient_roles = {
            role: {ref for ref, address in recipients.items() if address in addresses}
            for role, addresses in roles.items()
        }
        runtime.email_draft_prepared = True
        goal["status"] = "workflow"
        runtime.state[KEY] = goal
        result = await runtime.workflow(
            PrepareWorkflow(
                intent="compose",
                **{role + "_refs": sorted(refs) for role, refs in runtime.recipient_roles.items()},
            )
        )
    else:
        if args.draft is None:
            raise ValueError("Generate a subject and body now; a name suffices for text drafting")
        if args.draft.sources:
            raise ValueError("A user-only text draft has no source message numbers")
        goal["status"] = "drafted"
        goal["draft"] = args.draft.model_dump()
        result = {
            "kind": "message",
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
