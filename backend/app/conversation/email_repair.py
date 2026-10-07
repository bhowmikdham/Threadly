"""Operation-preserving, bounded repair for model-only drafting tools."""

from pydantic import ValidationError

from app.conversation import email_draft
from app.schemas.conversation import TOOLS


def observation(runtime, name, values, exc):
    values = values if isinstance(values, dict) else {}
    errors = exc.errors() if isinstance(exc, ValidationError) else []
    fields = sorted(
        {
            str(e["loc"][0])
            for e in errors
            if e["loc"] and e["loc"][0] in TOOLS[name][0].model_fields
        }
    )
    reason = getattr(exc, "reason", getattr(exc, "code", "email_draft_invalid_input"))
    if any(e["type"] == "email_goal_operation_required" for e in errors):
        reason = "email_goal_operation_required"
    elif name == "start_email_draft" and {"goal_id", "continue_previous"} & values.keys():
        reason = "new_email_identity_not_allowed"
    elif name == "continue_email_draft" and not values.get("goal_id"):
        reason = "email_goal_selection_required"
    elif "request_source" in fields or (
        values.get("request_source")
        and getattr(runtime, "request", None)
        and values["request_source"] != runtime.request.instruction
    ):
        reason = "email_request_source_required"

    start = name == "start_email_draft" or (
        name == "prepare_email_draft"
        and not values.get("continue_previous")
        and not values.get("goal_id")
    )
    operation = (
        "Use start_email_draft for this new independent email. Omit goal_id and "
        "continue_previous; never inherit another draft's recipient or purpose."
        if start or reason in {"new_email_requires_start", "new_email_identity_not_allowed"}
        else "Use continue_email_draft with the intended owned goal_id for this answer or "
        "revision. Keep that goal's retained fields. Do not start another goal to revise one."
    )
    if reason == "email_goal_operation_required":
        operation = (
            "Preserve the current USER operation: start_email_draft for a new email with no "
            "existing identity; continue_email_draft with goal_id for an existing email. "
            "Do not turn a new request into continuation because an old goal is available."
        )
    elif reason == "cancel_requires_email_goal":
        operation = "Cancel through continue_email_draft with the intended owned goal_id."
    elif reason in {"conversation_goal_missing", "conversation_goal_kind"}:
        operation = (
            "Use list_conversation_goals to find the intended retained email in this owned chat. "
            "Continue only its actual goal_id; ask which draft if ambiguous. Do not guess an ID."
        )
    if reason == "source_bound_workflow_required":
        message = "Use prepare_workflow for the source-based draft; preserve the read source."
    elif reason == "draft_artifact_required":
        message = "Use revise_draft or review_email_draft for the existing saved artifact."
    else:
        message = operation + (
            " Copy the complete current USER turn into request_source. Copy new fields only "
            "from USER text or verified USER citations. Leave genuinely missing details empty. "
            "When recipient and purpose are known, include draft={subject,body,"
            "unresolved_fields:[],sources:[]}. Omit draft while either required detail is missing. "
            "Do not repeat an unchanged invalid call. Never show this diagnostic to the user."
        )
    return {
        "error": reason,
        "fields": fields,
        "message": message,
        "pending_email_draft": email_draft.model_context(getattr(runtime, "state", {})),
    }


def exhausted():
    return {
        "kind": "message",
        "text": "I couldn’t finish the draft card yet. "
        "I’ve kept your supplied details; please ask me to try again. "
        "This chat turn did not save anything in Gmail or send an email.",
        "error_code": "email_draft_not_prepared",
    }
