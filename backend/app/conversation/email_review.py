"""Read-only draft guidance. Conversation tools never dispatch a Gmail write."""

import re

from app.api.errors import ApiError
from app.conversation import email_draft


def requested(runtime):
    from app.calendar.conversation_guard import social_response

    request = getattr(runtime, "request", None)
    if not request:
        return False
    state = getattr(runtime, "state", {})
    if not (state.get(email_draft.KEY) or getattr(runtime, "artifact", None)):
        return False
    text = request.instruction.strip()
    if social_response(text):
        return False
    if re.search(r"\b(?:new|another|different)\s+(?:email|e-mail|message|draft)\b", text, re.I):
        return False
    if re.search(r"\b(?:cancel|never mind|nevermind|don't|do not)\b", text, re.I):
        return False
    # Only whole, narrow follow-ups short-circuit the model. Compound requests,
    # quoted message content and new goals still go through semantic routing.
    if re.fullmatch(
        r"(?:(?:yes|yep|please)\s+)*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:save|send|insert|create)\s+(?:(?:this|that|the|my)\s+)?(?:draft|craft|it|this|that)"
        r"(?:\s+(?:in|to)\s+gmail)?(?:\s+or\s+(?:save|send|create)\s+"
        r"(?:this|that|the)\s+(?:draft|craft))?(?:\s+(?:please|for me))?|"
        r"(?:has (?:the draft|it) been saved|is (?:the draft|it) saved|"
        r"did you save (?:it|the draft))|"
        r"I (?:can't|cannot) see (?:the |a )?(?:draft |Create draft )?(?:button|card)",
        text.rstrip("?.! "),
        re.I,
    ):
        return True
    history = state.get("history", [])
    last = history[-1] if history else {}
    # An acknowledgment can request guidance only after the save discussion.
    # It never grants write authority, including in legacy promise-only chats.
    return bool(
        re.fullmatch(
            r"(?:(?:yes|yep|yeah|sure|ok|okay|thanks|thank you|please|do|it|that|go|ahead)"
            r"[,!. ]*)+",
            text,
            re.I,
        )
        and (
            last.get("email_draft_review")
            or re.search(
                r"\b(?:save|saved|saving)\b.{0,60}\b(?:draft|Gmail)\b",
                last.get("assistant", ""),
                re.I,
            )
        )
    )


async def context(runtime, session):
    from app.actions import gmail_draft
    from app.schemas.assistant import DraftOptions

    source = None
    text_draft = email_draft.current_text_draft(runtime.state)
    if text_draft:
        source = ("conversation", text_draft["draft_id"])
    elif runtime.artifact and runtime.artifact.payload.get("kind") == "draft":
        source = ("task", runtime.artifact.task_id)
    receipt = None
    status = "not_saved"
    if source:
        try:
            receipt = await gmail_draft.lookup(session, runtime.owner, *source)
        except ApiError as exc:
            if exc.code != "google_connection_changed":
                raise
            status = "connection_changed"
        else:
            if receipt:
                status = receipt["state"]
                if runtime.artifact and receipt.get("source_artifact_id") != runtime.artifact.id:
                    status = "earlier_revision"
    caps = runtime.capabilities.get("capabilities", [])
    draft_cap = next((c for c in caps if c["id"] == "gmail_draft"), {})
    recipient_needs_confirmation = False
    if text_draft:
        try:
            DraftOptions(to=[text_draft["recipient"]])
        except ValueError:
            recipient_needs_confirmation = True
    return {
        "execution": "user_card_click_only",
        "chat_can_save": False,
        "chat_can_send": False,
        "source": {"kind": source[0], "id": source[1]} if source else None,
        "save_status": status,
        "save_id": receipt["save_id"] if receipt else None,
        "gmail_draft_capability": draft_cap.get("status", "unavailable"),
        "create_control": "Create draft",
        "permission_control": "Enable draft creation",
        "installed_client_verified": False,
        "recipient_needs_confirmation": recipient_needs_confirmation,
    }


async def review(runtime):
    async with runtime.factory() as session:
        value = await context(runtime, session)
    runtime.email_draft_review = value
    status = value["save_status"]
    if status == "succeeded":
        text = (
            "This draft was saved in Gmail. Open Gmail Drafts to review or edit it. "
            "The save did not send it."
        )
    elif status in {"saving", "outcome_unknown"}:
        text = (
            "The draft save is still pending or its outcome is uncertain. "
            "Check Gmail Drafts before trying again; I haven’t confirmed another save."
        )
    elif status == "failed":
        text = (
            "The draft save failed. Review its card for the error; "
            "nothing has been confirmed saved."
        )
    elif status == "earlier_revision":
        text = (
            "An earlier revision has a save attempt. Check its status and Gmail Drafts "
            "before editing there; this chat revision has not been confirmed saved."
        )
    elif status == "connection_changed":
        text = (
            "Your Google connection changed. "
            "Reload the draft and check its status before continuing."
        )
    elif not value["source"]:
        text = (
            "I haven’t created a draft card or saved anything in Gmail for this request. "
            "The earlier chat text is not a saved Gmail draft. I still have your drafting request; "
            "ask me to prepare the draft again using those details."
        )
    else:
        text = "Your draft is still in this chat. "
        if value["gmail_draft_capability"] in {
            "scope_missing",
            "scope_unknown",
            "reconnect_required",
        }:
            text += (
                "Use Enable draft creation on its card to connect draft access, then review "
                "the recipient, subject and body and click Create draft separately. "
            )
        elif value["gmail_draft_capability"] != "ready":
            text += "Gmail draft saving is unavailable for this connection; you can copy the text. "
        else:
            text += (
                "Confirm the exact recipient address, review the subject and body, "
                if value["recipient_needs_confirmation"]
                else "Review the recipient, subject and body, "
            ) + "then click Create draft on its card. "
        text += (
            "Chat replies don’t save or send it. If the card or button is missing or disabled, "
            "update/reload the extension and reopen this chat."
        )
    return {"kind": "message", "text": text, "email_draft_review": value}


def unsupported_promise(text):
    return bool(
        re.search(
            r"\bI(?:'ll| will| can| have|'ve)?\s+"
            r"(?:save|saved|send|sent|insert|inserted)\b"
            r".{0,90}\b(?:draft|Gmail|it|this|that)\b|"
            r"\bI(?:'ll| will| can| have|'ve)?\s+(?:create|created)\b.{0,90}\bGmail\b|"
            r"\bdraft\b.{0,60}\b(?:will be ready|has been saved|is saved|was saved)\b",
            text,
            re.I,
        )
    )
