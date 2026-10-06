"""Route Calendar replies through preparation or owned durable action state."""

import re
from datetime import UTC, datetime


class CalendarPreparationRequired(ValueError):
    """A Calendar creation turn needs a typed event result, not a prose promise."""


class CalendarNewGoalRequired(ValueError):
    """A complete new creation directive must not inherit the previous draft."""


def social_response(text):
    # Only complete standalone phrases qualify. A greeting/thanks prefix must
    # never hide an event request, correction, status question or quoted source.
    value = " ".join(text.strip().casefold().split()).rstrip(".! ")
    if re.fullmatch(r"(?:no,? (?:thank you|thanks)|that(?:'s| is) all(?:,? thanks)?)", value):
        return {"kind": "message", "text": "All right. Take care!"}
    if re.fullmatch(
        r"(?:thanks(?: a lot| so much)?|thank you(?: very much| so much)?)"
        r"(?:,? (?:threadly|alfred))?",
        value,
    ):
        return {"kind": "message", "text": "You're welcome."}
    return None


def creation_turn(text):
    # This recognizes routing, not write authorization. prepare() still performs
    # the complete literal-source and exact-payload approval checks.
    from app.calendar.event_creation import creation_request, creation_target

    target = creation_target(text)
    if not target:
        return False
    if creation_request(text):
        return True
    clock = re.search(r"\b\d{1,2}(?::[0-5]\d)?\s*(?:[ap]\.?\s*m\.?)?", target)
    day = re.search(
        r"\b(?:today|tomorrow|tmrw|tmr|(?:next\s+)?"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d{4}-\d{2}-\d{2})\b",
        target,
    )
    if not clock and not day:
        return False
    # Derive candidate spans solely to ask for the proper tool. They never become
    # event fields or grant permission. The tool must supply independently checked fields.
    title_first = re.split(r"\s+(?:at|on|tomorrow|today)\b", target, maxsplit=1)[0]
    time_first = re.split(r"\s+for\s+", target)
    candidates = [title_first]
    if len(time_first) >= 2:
        candidates.append(re.sub(r"[?.!]+$", "", time_first[-1]))
    candidates.append("")
    return any(
        creation_request(text, title, day[0] if day else "", clock[0].strip() if clock else "")
        for title in candidates
    )


def pending_event(runtime):
    state = getattr(runtime, "state", {})
    pending = state.get("calendar_event_request") if isinstance(state, dict) else None
    if pending and datetime.fromisoformat(pending["expires_at"]) > datetime.now(UTC):
        return pending
    return None


def pending_reply(text, pending):
    if not pending or pending.get("action_id") or len(text) > 500:
        return False
    if re.fullmatch(r"\s*(?:thanks|thank you|ok|okay|never mind|cancel)[.! ]*", text, re.I):
        return False
    if re.match(
        r"\s*(?:summari[sz]e|draft|reply|explain|search|find|read|write|create|book|schedule|reserve|what|why|how|check|show|list|am|is|are|do|does|did|have|can|could|will|would)\b",
        text,
        re.I,
    ):
        return False
    # A short answer to the retained event's missing field should use its tool.
    return not text.rstrip().endswith("?")


def requires_preparation(runtime):
    request = getattr(runtime, "request", None)
    return bool(
        request
        and (
            creation_turn(request.instruction)
            or pending_reply(request.instruction, pending_event(runtime))
        )
    )


def status_question(text, has_event):
    value = text.strip().casefold().rstrip("?.! ")
    if not has_event and not re.search(r"\b(?:event|appointment)\b", value):
        return False
    return bool(
        (has_event and value in {"check now", "check again", "status", "is it done", "done"})
        or re.fullmatch(
            r"(?:(?:did|have) you (?:create|created|book|booked|schedule|scheduled) "
            r"(?:it|the event|my appointment)|is (?:it|the event|my appointment) "
            r"(?:created|booked|scheduled)|(?:check|show) (?:its|the event) status)",
            value,
        )
    )


async def respond(runtime, answer):
    request = getattr(runtime, "request", None)
    if request is None:
        return None
    text = request.instruction
    if acknowledgment := social_response(text):
        return acknowledgment
    state = getattr(runtime, "state", {})
    pending = pending_event(runtime)
    if pending and re.fullmatch(r"\s*(?:cancel|never mind|nevermind)[.! ]*", text, re.I):
        from app.calendar.event_creation import prepare
        from app.schemas.conversation import PrepareCalendarEvent

        return await prepare(
            runtime,
            PrepareCalendarEvent(
                continue_previous=True, intent={"operation": "cancel", "source": text}
            ),
        )
    action_id = state.get("last_calendar_action_id")
    history = state.get("history", [])
    # A newer unfinished/failed event request supersedes the previous action.
    # Never answer its status with an older successful booking from this chat.
    if pending:
        action_id = pending.get("action_id")
    else:
        for entry in reversed(history):
            if entry.get("calendar_action_id"):
                break
            if (
                creation_turn(entry.get("user", ""))
                or entry.get("error_code") == "calendar_event_not_prepared"
            ):
                action_id = None
                break
    active_event_context = bool(
        pending or (action_id and history and history[-1].get("calendar_action_id") == action_id)
    )
    if status_question(text, active_event_context):
        if action_id:
            from app.calendar.event_creation import response

            async with runtime.factory() as session:
                return await response(session, runtime.owner, action_id)
        if pending:
            pending["last_request_id"] = request.request_id
        return {
            "kind": "message",
            "text": "I don't have a confirmed Calendar event for this request yet.",
        }
    if creation_turn(text) or pending_reply(text, pending):
        raise CalendarPreparationRequired
    # Source-less prose cannot establish a provider-side result. Existing direct
    # actions always use current owned durable state, including queued/unknown/failed.
    if not answer.evidence and re.search(
        r"\b(?:your|the) (?:event|appointment|meeting)\b.{0,40}"
        r"\b(?:created|booked|scheduled|added)\b|"
        r"\bI(?:'ve| have)? (?:created|booked|scheduled|added) "
        r"(?:your|the|an|a) (?:event|appointment|meeting)\b",
        answer.text,
        re.I,
    ):
        if action_id:
            from app.calendar.event_creation import response

            async with runtime.factory() as session:
                return await response(session, runtime.owner, action_id)
        return {"kind": "message", "text": "I haven't confirmed that an event was created."}
    return None


def exhausted(runtime):
    request = getattr(runtime, "request", None)
    pending = pending_event(runtime)
    if request and (
        creation_turn(request.instruction) or pending_reply(request.instruction, pending)
    ):
        if pending and pending.get("action_id") and creation_turn(request.instruction):
            runtime.state.pop("calendar_event_request", None)
            pending = None
        if pending:
            pending["last_request_id"] = request.request_id
        return {
            "kind": "message",
            "text": "I couldn't prepare this event yet. I haven't confirmed a booking. "
            "Please try again.",
            "error_code": "calendar_event_not_prepared",
        }
    return None
