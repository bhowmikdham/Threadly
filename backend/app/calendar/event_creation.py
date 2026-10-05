"""Direct user-requested event candidates. External writes remain in the action worker."""

import re
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.actions import calendar_preview
from app.actions import service as actions
from app.api.errors import ApiError
from app.assistant.summary import digest
from app.calendar import permissions, service
from app.calendar.conversation_tools import RequestClarification, duration, literal, resolve_window
from app.calendar.time_resolution import parse_clock, wall_instants
from app.capabilities.service import build_capabilities
from app.conversation import store
from app.db.models import (
    ArtifactRevision,
    AssistantAction,
    AssistantTask,
    CalendarPreference,
    TaskEvent,
    User,
)
from app.schemas.actions import ApproveActionRequest
from app.schemas.calendar_tools import CalendarWindow

POLICY = "direct-calendar-event-1.0.0"


def creation_request(text):
    text = text.casefold().replace("craete", "create").replace("creat ", "create ")
    if re.search(r"\b(?:don't|do not|never|cancel|delete|remove|reschedule|update)\b", text):
        return False
    return bool(
        re.search(
            r"(?:^|\n)\s*(?:(?:please|hey)[, ]+)?"
            r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
            r"(?:i(?:'d)?\s+(?:want|need|like)\s+(?:you\s+)?to\s+))?"
            r"(?:create|add|book|schedule|put|block)\s+",
            text,
        )
    )


def source_fields(args, text):
    if re.search(r"\b(?:every|recurring|daily|weekly|monthly|yearly|repeat)\b", text, re.I):
        raise RequestClarification("I can create one-time events. Which single date should I use?")
    for value in (args.title, args.location, args.description, args.calendar_name):
        if value:
            literal(value, text)
    for address in args.attendees:
        literal(address, text)
    # Fail closed when an extracted candidate omits an explicit constraint. Only
    # literal user-authored fields are removed; provider content never enters here.
    remainder = text.casefold()
    for value in (
        args.title,
        args.date_source,
        args.time_source,
        args.duration_phrase,
        args.calendar_name,
        args.location,
        args.description,
        *args.attendees,
    ):
        if value:
            remainder = remainder.replace(value.casefold(), " ")
    if args.attendees:
        remainder = re.sub(r"\binvite\b", " ", remainder)
    if re.search(
        r"\b(?:every|recurring|daily|weekly|monthly|yearly|repeat|until|ending|ends|"
        r"timezone|utc|gmt|hours?|minutes?|mins?|tomorrow|today|tonight|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|invite)\b|"
        r"\b(?:at|from|to|between|on)\s+\d|\d{1,2}:\d{2}|"
        r"\d\s*(?:am|pm)\b|[^\s@]+@[^\s@]+|[A-Za-z]+/[A-Za-z_]+",
        remainder,
    ):
        raise RequestClarification(
            "Please give one event's title, date, start time and any duration or guest email "
            "addresses. I couldn't preserve all the requested details."
        )
    if args.time_source:
        literal(args.time_source, text)
        if len(parse_clock(args.time_source)) != 1 or parse_clock(args.time_source) != parse_clock(
            args.time
        ):
            raise RequestClarification("What time should it start? Please include AM or PM.")


def resolve_times(args, text, preferences, anchor):
    if not args.date or not args.time:
        raise RequestClarification("What day and time should I use?")
    literal(args.date_source, text)
    window = CalendarWindow(subject="self", date=args.date, date_source=args.date_source)
    start_day, end_day = resolve_window(window, args.date_source, anchor, preferences["timezone"])
    zone = ZoneInfo(preferences["timezone"])
    if (end_day.astimezone(zone).date() - start_day.astimezone(zone).date()).days != 1:
        raise RequestClarification("Choose one date for this event.")
    clocks = parse_clock(args.time)
    if len(clocks) != 1:
        raise RequestClarification("What time should it start? Please include AM or PM.")
    starts = wall_instants(start_day.astimezone(zone).date(), clocks[0], zone)
    if len(starts) != 1:
        raise RequestClarification(
            "That local time is ambiguous or does not exist. Choose another time."
        )
    minutes = duration(args.duration_phrase, text, preferences["default_duration_minutes"])
    start, end = starts[0], starts[0] + timedelta(minutes=minutes)
    if start <= datetime.now(UTC) or end > datetime.now(UTC) + timedelta(days=90):
        raise RequestClarification("Choose a future event within the next 90 days.")
    return start, end


async def prepare(runtime, args):
    from app.schemas.conversation import PrepareCalendarEvent

    # Only USER instructions can establish intent and event fields. Provider/email
    # content, model history and the approval mode are never accepted as authority.
    pending = runtime.state.get("calendar_event_request") if args.continue_previous else None
    if pending and datetime.fromisoformat(pending["expires_at"]) <= datetime.now(UTC):
        pending = None
    if args.continue_previous and not pending:
        raise RequestClarification("Please repeat the event details for this new request.")
    text = runtime.request.instruction
    anchor = runtime.calendar_anchor
    if pending:
        text = pending["user_text"] + "\n" + text
        anchor = datetime.fromisoformat(pending["anchor"])
        values = dict(pending["arguments"])
        values.update(
            {
                k: v
                for k, v in args.model_dump(exclude_unset=True).items()
                if k != "continue_previous"
            }
        )
        args = PrepareCalendarEvent.model_validate(values)
    if not creation_request(text):
        raise RequestClarification("Tell me the event you want to create.")
    try:
        source_fields(args, text)
    except (RequestClarification, ValueError) as error:
        return {"kind": "clarification", "text": str(error)}
    runtime.state["calendar_event_request"] = {
        "arguments": args.model_dump(mode="json"),
        "user_text": text[-6000:],
        "anchor": anchor.isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        "last_request_id": runtime.request.request_id,
    }
    if not args.title:
        return {"kind": "clarification", "text": "What should I call the event?"}
    async with runtime.factory() as session:
        user = await session.get(User, runtime.owner)
        caps = {c["id"]: c for c in build_capabilities(user)["capabilities"]}
        if not caps["calendar_write"]["ready"]:
            return {
                "kind": "message",
                "text": "Enable Calendar event creation, then I can prepare this event."
                if caps["calendar_write"]["enabled"]
                else "Calendar event creation is not enabled for this account yet.",
                "calendar_connection_required": caps["calendar_write"]["enabled"],
                "error_code": "calendar_write_scope_required"
                if caps["calendar_write"]["enabled"]
                else "calendar_writes_disabled",
            }
        pref = await session.get(CalendarPreference, runtime.owner)
        if pref is None or service.pref_view(pref, user.google_account_version).needs_review:
            return {
                "kind": "message",
                "text": "Review Calendar settings so I can use the right calendar and timezone.",
                "error_code": "calendar_preferences_missing",
            }
        preferences, pref_version, account_version = (
            pref.preferences,
            pref.version,
            user.google_account_version,
        )
    try:
        start, end = resolve_times(args, text, preferences, anchor)
    except (RequestClarification, ValueError) as error:
        return {"kind": "clarification", "text": str(error)}
    key = str(
        uuid5(
            NAMESPACE_URL,
            f"calendar-event:{runtime.owner}:{runtime.request.conversation_id}:"
            f"{runtime.request.request_id}",
        )
    )
    async with runtime.factory() as session:
        old = await session.scalar(
            select(AssistantAction).where(
                AssistantAction.user_id == runtime.owner, AssistantAction.proposal_request_id == key
            )
        )
        if old:
            return await response(session, runtime.owner, old.id)
    result = await service.list_calendars(runtime.owner)
    choices = [
        c
        for c in result["calendars"]
        if c["event_write_acl"] and c["id"] in preferences["calendar_ids"]
    ]
    if args.calendar_name:
        choices = [c for c in choices if c["summary"].casefold() == args.calendar_name.casefold()]
    elif any(c.get("primary") for c in choices):
        choices = [c for c in choices if c.get("primary")]
    if not choices:
        return {
            "kind": "message",
            "text": "Choose a calendar you can edit in Calendar settings, then try again.",
            "error_code": "calendar_preferences_missing",
        }
    if len(choices) != 1:
        return {
            "kind": "clarification",
            "text": "Which calendar should I use? " + ", ".join(c["summary"] for c in choices[:10]),
        }
    async with runtime.factory.begin() as session:
        action = await propose(
            session,
            runtime,
            args,
            key,
            choices[0],
            start,
            end,
            preferences,
            pref_version,
            account_version,
        )
        result = await response(session, runtime.owner, action.id)
        runtime.state.pop("calendar_event_request", None)
        runtime.state["last_calendar_action_id"] = action.id
        await store.checkpoint(
            session, runtime.owner, runtime.request, runtime.lease, runtime.state, result
        )
        return result


async def propose(
    session, runtime, args, key, calendar, start, end, preferences, pref_version, account_version
):
    owner = runtime.owner
    # A deterministic task owns this immutable event candidate and its action history.
    task_id = str(uuid5(NAMESPACE_URL, key + ":task"))
    artifact_id = str(uuid5(NAMESPACE_URL, key + ":artifact"))
    task = await session.get(AssistantTask, task_id, with_for_update=True)
    if task:
        old = await session.scalar(
            select(AssistantAction).where(
                AssistantAction.user_id == owner, AssistantAction.proposal_request_id == key
            )
        )
        if old:
            return old
        raise ApiError(409, "calendar_candidate_changed", "Retry the current event request.")
    user = await calendar_preview.ready_account(session, owner, lock=True)
    pref = await session.get(CalendarPreference, owner, populate_existing=True)
    if (
        user.google_account_version != account_version
        or pref is None
        or pref.version != pref_version
    ):
        raise service.conflict()
    chat = await store.owned(session, owner, runtime.request.conversation_id)
    policy = permissions.view(chat, user)
    event = {
        "id": uuid5(NAMESPACE_URL, key).hex,
        "summary": args.title,
        "description": args.description,
        "location": args.location,
        "start": {"dateTime": start.isoformat(), "timeZone": preferences["timezone"]},
        "end": {"dateTime": end.isoformat(), "timeZone": preferences["timezone"]},
        "attendees": [{"email": address} for address in args.attendees],
        "extendedProperties": {"private": {"threadlyAction": digest({"owner": owner, "key": key})}},
        "reminders": {"useDefault": False},
        "guestsCanModify": False,
        "guestsCanInviteOthers": False,
        "guestsCanSeeOtherGuests": True,
    }
    payload = {
        "calendar_id": calendar["id"],
        "calendar_name": calendar["summary"],
        "send_updates": "all" if args.attendees else "none",
        "event": event,
    }
    task = AssistantTask(
        id=task_id,
        user_id=owner,
        request_id=key,
        request_hash=digest(payload),
        instruction=runtime.request.instruction,
        intent_hint="plan_schedule",
        state="succeeded",
        version=1,
        latest_sequence=1,
        release={"workflow": POLICY},
    )
    session.add(task)
    await session.flush()
    artifact = ArtifactRevision(
        id=artifact_id,
        task_id=task_id,
        user_id=owner,
        revision=1,
        payload={"kind": "calendar_event", "content": payload},
        provenance={"policy": POLICY},
        draft_envelope=None,
    )
    session.add(artifact)
    await session.flush()
    task.final_artifact_id = artifact.id
    session.add(
        TaskEvent(
            task_id=task.id,
            user_id=owner,
            sequence=1,
            task_version=1,
            kind="task.succeeded",
            payload={"artifact_id": artifact.id},
        )
    )
    now = await session.scalar(select(func.clock_timestamp()))
    action = await actions.propose(
        session,
        owner,
        artifact.id,
        request_id=key,
        expected_revision=1,
        action_type="create_event",
        payload_schema=calendar_preview.SCHEMA,
        payload=payload,
        source_versions={
            "direct": True,
            "conversation_id": chat.id,
            "account_version": user.google_account_version,
            "google_subject": user.google_sub,
            "preferences_version": pref.version,
            "session_version": user.threadly_session_version,
            "approval_mode": policy["mode"],
            "permission_version": policy["version"],
        },
        expires_at=min(now + timedelta(minutes=10), start),
    )
    if policy["mode"] == "always":
        await calendar_preview.approve(
            session,
            owner,
            action.id,
            ApproveActionRequest(
                request_id=key + ":auto",
                expected_version=action.version,
                payload_hash=action.payload_hash,
            ),
        )
    return action


async def blockers(session, owner, action):
    reasons = []
    expected = action.source_versions
    user = await calendar_preview.ready_account(session, owner)
    pref = await session.get(CalendarPreference, owner, populate_existing=True)
    if (
        user.google_account_version != expected["account_version"]
        or user.google_sub != expected["google_subject"]
        or user.threadly_session_version != expected["session_version"]
    ):
        reasons.append("calendar_account_changed")
    if (
        pref is None
        or pref.version != expected["preferences_version"]
        or action.payload["calendar_id"] not in pref.preferences["calendar_ids"]
    ):
        reasons.append("calendar_preferences_changed")
    if expected["approval_mode"] == "always":
        chat = await store.owned(session, owner, expected["conversation_id"])
        policy = permissions.view(chat, user)
        if policy["mode"] != "always" or policy["version"] != expected["permission_version"]:
            reasons.append("calendar_permission_changed")
    artifact = await session.get(ArtifactRevision, action.artifact_id)
    if (
        artifact is None
        or digest({"artifact": artifact.payload, "envelope": artifact.draft_envelope})
        != action.source_artifact_hash
    ):
        reasons.append("booking_artifact_changed")
    return reasons


async def response(session, owner, action_id):
    action = await calendar_preview.view(session, owner, action_id)
    messages = {
        "proposed": "Review this event before creating it.",
        "approved": "Event creation is queued. I'll show the result here.",
        "executing": "Creating your event…",
        "succeeded": "Your event was created.",
        "outcome_unknown": "Google hasn't confirmed the result yet. Check this event's status "
        "before trying again.",
        "cancelled": "Event creation was cancelled.",
        "rejected": "This event wasn't created.",
        "expired": "This event preview expired. Ask me to prepare it again.",
        "superseded": "Calendar settings or availability changed. Prepare this event again.",
        "failed": "This event couldn't be created.",
    }
    return {
        "kind": "calendar_event",
        "text": messages.get(action["state"], "Check the event status below."),
        "calendar_action": action,
        "calendar_action_id": action_id,
    }
