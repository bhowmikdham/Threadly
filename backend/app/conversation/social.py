"""Bounded social turns; no source reads, goal mutation, inference or action authority."""

import re

POLICY = "social-replies-1.0:full-turn-only:preserve-pending-content:no-action-authority"


def kind(text):
    text = re.sub(r"[^\w\s]", " ", str(text).lower().replace("’", "").replace("'", ""))
    text = " ".join(text.split())
    greeting = (
        r"(?:(?:hey|hi|hello|good morning|good afternoon|good evening)"
        r"(?: there)?(?: threadly)?\s*)?"
    )
    if re.fullmatch(
        greeting + r"how(?: are you| are you doing|s it going| is it going)(?: today)?", text
    ):
        return "wellbeing"
    if re.fullmatch(
        r"(?:hey|hi|hello|good morning|good afternoon|good evening)(?: there)?(?: threadly)?", text
    ):
        return "greeting"
    if re.fullmatch(
        r"(?:(?:im|i am) )?(?:good|fine|well|not bad)"
        r"(?: thanks| thank you)?(?: and you| how about you)?",
        text,
    ):
        return "reciprocal"
    if re.fullmatch(r"(?:thanks|thank you)(?: threadly| so much)?", text):
        return "thanks"
    if re.fullmatch(r"(?:no thank you|no thanks|thats all|bye|goodbye|thanks thats all)", text):
        return "closing"
    if re.fullmatch(r"(?:who are you|whats your name|what is your name)", text):
        return "identity"
    return None


REPLIES = {
    "greeting": ("Hey! What can I help with?", "Hi! What’s on your mind?"),
    "wellbeing": (
        "Hey! I’m here and ready to help. How are you?",
        "Hi! Ready when you are. How’s your day going?",
    ),
    "reciprocal": (
        "Glad to hear it. What would you like to work on?",
        "Good to hear. What can I help with?",
    ),
    "thanks": ("You’re welcome!", "Anytime."),
    "closing": ("Okay, take care!", "All right. Talk later!"),
    "identity": ("I’m Threadly, your email and calendar assistant.",),
}


def reply(context):
    category = kind(context.get("user_turn", ""))
    if not category:
        return None
    # A short greeting may be the message body/title the user was asked for.
    # Keep that ambiguity with the ordinary, context-aware conversation tools.
    work = context.get("active_work") or {}
    email = context.get("pending_email_draft") or {}
    awaiting = (
        work.get("state") == "needs_clarification"
        or email.get("status") == "clarification"
        or context.get("pending_calendar_event")
        or context.get("pending_mail_reply")
    )
    if awaiting:
        return None
    history = context.get("recent_dialogue") or []
    if category == "reciprocal":
        last = history[-1].get("assistant", "") if history else ""
        if not re.search(r"how(?: are you| is your day|[’']s your day)|how about you", last, re.I):
            return None
    choices = REPLIES[category]
    previous = sum(kind(item.get("user", "")) == category for item in history)
    return {"kind": "message", "text": choices[previous % len(choices)]}
