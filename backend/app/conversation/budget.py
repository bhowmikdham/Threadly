"""Bound derived model context; never alter stored dialogue, goals or artifacts."""

import json
from copy import deepcopy

MAX_CONTEXT_CHARS = 48000


def fit(context):
    result = deepcopy(context)
    omitted = []

    def size():
        return len(json.dumps(result))

    while size() > MAX_CONTEXT_CHARS and result.get("recent_dialogue"):
        removed = result["recent_dialogue"].pop(0)
        omitted.append(f"dialogue:{removed.get('version', 'legacy')}")
    for field in (
        "remembered_email_sources",
        "pending_mail_goal",
        "pending_mail_reply",
        "pending_calendar_event",
        "pending_email_draft",
    ):
        if size() <= MAX_CONTEXT_CHARS:
            break
        value = result.get(field)
        if not value:
            continue
        # State is recoverable through the owned goal registry. This is an
        # explicit omission, never an apparently complete but clipped value.
        result[field] = {
            "omitted_from_context": True,
            "retrieve_with": "select_conversation_goal",
        }
        omitted.append(field)
    if size() > MAX_CONTEXT_CHARS and result.get("active_work"):
        active = result["active_work"]
        result["active_work"] = {
            k: active[k] for k in ("task_id", "state", "proposal_id") if k in active
        }
        result["active_work"]["retrieve_with"] = "select_conversation_goal"
        omitted.append("active_work_content")
    if omitted:
        result["context_budget"] = {
            "omitted": omitted,
            "notice": (
                "Derived context omitted older data. Retrieve before using it; "
                "originals remain saved."
            ),
        }
    return result
