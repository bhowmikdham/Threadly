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
from app.calendar import (
    date_grounding,
    event_choices,
    event_draft,
    event_timezone,
    intent,
    permissions,
    service,
)
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

POLICY = "direct-calendar-event-1.2.1"


user_directive = intent.user_directive


def source_fields(args, text, previous_calendar_names=(), *, constraints=True):
    if constraints and re.search(
        r"\b(?:every|recurring|daily|weekly|monthly|yearly|repeat)\b", text, re.I
    ):
        raise RequestClarification("I can create one-time events. Which single date should I use?")
    if constraints:
        complete_trailing_title(args, text)
    for field in ("title", "location", "description", "calendar_name", "duration_phrase"):
        value = getattr(args, field)
        if value:
            event_draft.literal_field(value, text, field)
    if args.date_source:
        event_draft.literal_field(args.date_source, text, "date")
    if args.timezone:
        event_draft.literal_field(args.timezone_source, text, "timezone")
        if event_timezone.zone_name(args.timezone) != event_timezone.zone_name(
            args.timezone_source
        ):
            raise event_draft.FieldRepairRequired("timezone", interpretation=True)
    for address in args.attendees:
        event_draft.literal_field(address, text, "attendees")
    if args.time_source:
        event_draft.literal_field(
            args.time_source, re.sub(r"\bat(?=\d)", "at ", text, flags=re.I), "time"
        )
        # Ambiguous user wording needs a question; a conflicting model value needs repair.
        try:
            source_clock = parse_clock(event_timezone.clock_source(args.time_source))
        except ValueError:
            source_clock = []
        if len(source_clock) != 1:
            raise RequestClarification("What time should it start? Please include AM or PM.")
        try:
            interpreted_clock = parse_clock(args.time)
        except ValueError:
            interpreted_clock = []
        if source_clock != interpreted_clock:
            raise event_draft.FieldRepairRequired("time", interpretation=True)
    if not constraints:
        return
    # Fail closed when an extracted candidate omits an explicit constraint. Only
    # literal user-authored fields are removed; provider content never enters here.
    remainder = text.casefold()
    for value in (
        args.title,
        args.date_source,
        args.time_source,
        args.timezone_source,
        args.duration_phrase,
        args.calendar_name,
        args.location,
        args.description,
        *args.attendees,
        *previous_calendar_names,
    ):
        if value:
            remainder = remainder.replace(value.casefold(), " ")
    if args.attendees:
        remainder = re.sub(r"\binvite\b", " ", remainder)
    if re.search(
        r"\b(?:every|recurring|daily|weekly|monthly|yearly|repeat|until|ending|ends|"
        r"timezone|utc|gmt|aest|aedt|hours?|minutes?|mins?|tomorrow|today|tonight|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|invite)\b|"
        r"\b(?:at|from|to|between|on)\s+\d|\d{1,2}:\d{2}|"
        r"\d\s*[ap]\.?\s*m\b|[^\s@]+@[^\s@]+|[A-Za-z]+/[A-Za-z_]+",
        remainder,
    ):
        raise RequestClarification(
            "Please give one event's title, date, start time and any duration or guest email "
            "addresses. I couldn't preserve all the requested details."
        )


def complete_trailing_title(args, text):
    # Narrow unambiguous form: "make an event at <time> <date> for <title>".
    # Do not infer titles when dates or other fields still follow the marker.
    if not args.title:
        return

    quoted = re.search(
        r"\b(?:for|called|named|titled|title(?:\s+to)?|event)\s+"
        r"(?:\"([^\"\n]+)\"|“([^”\n]+)”|'([^'\n]+)'|‘([^’\n]+)’)",
        text,
        re.I,
    )
    if quoted:
        literal_title = next(value for value in quoted.groups() if value is not None)
        if " ".join(args.title.casefold().split()) != " ".join(literal_title.casefold().split()):
            raise event_draft.IncompleteEventTitle(literal_title)

    # Explicit naming preserves its article even when the name was not quoted.
    # This targets article loss only; it never guesses a different title.
    named = re.search(
        r"\b(?:called|named|titled|title(?:\s+to)?)\s+[\"“'‘]?((?:a|an|the)\s+"
        + re.escape(args.title.strip("\"“”'‘’"))
        + r")(?!\w)",
        text,
        re.I,
    )
    if named:
        raise event_draft.IncompleteEventTitle(named[1])
    if not (args.date_source and args.time_source):
        return

    def normalized(value):
        return " ".join(value.casefold().split())

    for marker in re.finditer(r"\bfor\s+", text, re.I):
        # Repair validation can join original text, this turn and retained
        # evidence. Do not assemble a title pattern across those source lines.
        before = normalized(text[: marker.start()].rsplit("\n", 1)[-1])
        if any(normalized(source) not in before for source in (args.date_source, args.time_source)):
            continue
        raw_title = text[marker.end() :].split("\n", 1)[0].strip().rstrip(".!?").strip()
        title = raw_title.strip("\"“”'‘’")
        if re.fullmatch(r"(?:that|this|it|the meeting|the event)", title, re.I):
            continue
        other_fields = (
            args.duration_phrase,
            args.location,
            args.description,
            args.calendar_name,
            *args.attendees,
        )
        if (
            not title
            or len(title) > 300
            or any(value and normalized(value) in normalized(title) for value in other_fields)
        ):
            return
        # A lowercase indefinite article in a bare, unquoted description is
        # grammatical scaffolding. Preserve all remaining words, quoted names,
        # capitalized names and definite articles (e.g. The Office).
        generic = raw_title == title and re.match(r"^(?:a|an)\s+(?=[a-z])", title)
        without_article = title[generic.end() :] if generic else title
        if normalized(args.title.strip('"“”')) not in {
            normalized(title),
            normalized(without_article),
        }:
            raise event_draft.IncompleteEventTitle(title)
        return


def resolve_date(args, anchor, timezone):
    window = CalendarWindow(subject="self", date=args.date, date_source=args.date_source)
    zone = ZoneInfo(timezone)
    start, end = resolve_window(window, args.date_source, anchor, timezone)
    first, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    date_grounding.validate(args.date_source, first, last, anchor.astimezone(zone).date())
    if (last - first).days != 1:
        raise RequestClarification("Choose one date for this event.")
    return first


async def validate_date(runtime, args, saved, pending):
    """Reject a date contradiction before replacing state or retiring an action."""
    if not args.date:
        return
    timezone = saved.get("date_anchor_timezone")
    if (
        not timezone
        and pending
        and pending.get("action_id")
        and saved["anchor"] == pending["anchor"]
        and saved["arguments"]["date"] == pending["arguments"].get("date")
        and saved["arguments"]["date_source"] == pending["arguments"].get("date_source")
    ):
        # Pre-1.2.1 drafts pinned the resolved day but not its anchor zone. Recover
        # that zone only from the same owned immutable preview, never a new zone
        # correction or current preference that could move the original local day.
        async with runtime.factory() as session:
            old = await session.scalar(
                select(AssistantAction).where(
                    AssistantAction.id == pending["action_id"],
                    AssistantAction.user_id == runtime.owner,
                )
            )
            if old:
                timezone = old.payload["event"]["start"]["timeZone"]
    if not timezone and args.timezone:
        timezone = event_timezone.zone_name(args.timezone)
    if not timezone:
        async with runtime.factory() as session:
            pref = await session.get(CalendarPreference, runtime.owner)
            if pref is None:
                return  # The existing preference gate still blocks any preview.
            timezone = pref.preferences["timezone"]
    resolve_date(args, datetime.fromisoformat(saved["anchor"]), timezone)
    saved["date_anchor_timezone"] = timezone


def resolve_times(args, text, preferences, anchor, date_timezone=None):
    if not args.date or not args.time:
        raise RequestClarification("What day and time should I use?")
    literal(args.date_source, text)
    timezone = event_timezone.zone_name(args.timezone) if args.timezone else preferences["timezone"]
    day = resolve_date(args, anchor, date_timezone or timezone)
    zone = ZoneInfo(timezone)
    clocks = parse_clock(args.time)
    if len(clocks) != 1:
        raise RequestClarification("What time should it start? Please include AM or PM.")
    starts = wall_instants(day, clocks[0], zone)
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
    from app.conversation import citations

    intent.validate_source(args.intent, runtime.request.instruction)
    field_citations = await citations.resolve(runtime, args.citations)
    runtime.calendar_field_citations = field_citations
    args = event_timezone.bind(
        args,
        runtime.request.instruction
        + "\n"
        + "\n".join(value["source"] for value in field_citations.values()),
    )
    if (
        args.timezone
        and args.timezone_source not in runtime.request.instruction
        and "timezone" not in field_citations
        and "time" in field_citations
        and args.timezone_source in field_citations["time"]["source"]
    ):
        field_citations["timezone"] = field_citations["time"]
    current = event_choices.pending(runtime.state)
    # Retrying this turn's failed creation is a field repair, not a reset. A new
    # USER turn still starts a distinct goal; reviewed candidates stay immutable.
    repairing_create = (
        not args.continue_previous
        and current
        and current.get("goal_id") == runtime.request.request_id
        and current.get("last_request_id") == runtime.request.request_id
        and current.get("origin_pending_validation")
        and not current.get("action_id")
    )
    pending = current if args.continue_previous or repairing_create else None
    if args.continue_previous and not pending:
        return {
            "kind": "clarification",
            "text": "Please repeat the event details for this new request.",
        }
    # The typed operation selects the goal. No second positive language parser
    # may veto it based on the order of the user's words.
    origin = (pending or {}).get("creation_origin")
    if pending:
        if not origin:
            # Older owned structured drafts were validated before persistence.
            # This never reconstructs a goal from assistant prose or email history.
            origin = {"text": pending["user_text"], "policy": "legacy-stored-calendar-goal"}
    else:
        intent.validate_creation(runtime.request.instruction, args.title)
        origin = {"text": user_directive(runtime.request.instruction), "policy": intent.POLICY}
    if args.intent and args.intent.operation == "cancel":
        if args.intent.source.strip() != user_directive(runtime.request.instruction).strip():
            raise RequestClarification("Please confirm cancellation directly.")
        if not re.fullmatch(
            r"\s*(?:please\s+)?(?:cancel|stop|never mind|nevermind)"
            r"(?:\s+(?:it|that|this|the|my|pending|event|request|booking))*[.! ]*",
            args.intent.source,
            re.I,
        ):
            raise RequestClarification("Please confirm cancellation of this pending event.")
        await retire_candidate(runtime, pending, "cancelled")
        from app.conversation import goals

        goals.close(runtime.state, pending)
        runtime.state.pop("calendar_event_request", None)
        return {
            "kind": "message",
            "text": "Cancelled this pending event request. No new event was created.",
        }
    try:
        args, saved, changed = event_draft.merge(runtime, args, pending)
        await validate_date(runtime, args, saved, pending)
    except (
        event_draft.IntentSourceMismatch,
        event_draft.IncompleteEventTitle,
        event_draft.FieldRepairRequired,
    ) as error:
        # This is a model protocol error, not information missing from the user.
        # Keep the exact-source fence and let the bounded engine repair the call.
        event_draft.retain_partial(runtime, args, pending, origin, error)
        raise
    except (RequestClarification, ValueError) as error:
        event_draft.retain_partial(runtime, args, pending, origin, error)
        return {"kind": "clarification", "text": str(error)}
    saved["creation_origin"] = origin
    if pending and pending.get("action_id"):
        if not changed:
            async with runtime.factory() as session:
                return await response(session, runtime.owner, pending["action_id"])
        await retire_candidate(runtime, pending, "superseded")
        saved.pop("action_id", None)
    # A new independent goal changes focus. The previous goal and candidate stay
    # in the owned registry; only an explicit revision/cancellation retires them.
    runtime.state["calendar_event_request"] = saved
    text = saved["user_text"]
    anchor = datetime.fromisoformat(saved["anchor"])
    if not args.title:
        return {"kind": "clarification", "text": "What should I call the event?"}
    if not args.date:
        return {"kind": "clarification", "text": "What day should I use?"}
    if not args.time:
        return {
            "kind": "clarification",
            "text": "What time should it start? Please include AM or PM.",
        }
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
        start, end = resolve_times(
            args, text, preferences, anchor, saved.get("date_anchor_timezone")
        )
    except (RequestClarification, ValueError) as error:
        return {"kind": "clarification", "text": str(error)}
    saved["arguments"]["date"] = {
        "kind": "absolute",
        "start": start.astimezone(
            ZoneInfo(
                event_timezone.zone_name(args.timezone)
                if args.timezone
                else preferences["timezone"]
            )
        )
        .date()
        .isoformat(),
    }
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
    choices = event_choices.eligible(result["calendars"], preferences)
    calendar, clarification = event_choices.resolve(
        runtime.state, choices, pref_version, account_version, args.calendar_name
    )
    if clarification:
        return clarification
    async with runtime.factory.begin() as session:
        action = await propose(
            session,
            runtime,
            args,
            key,
            calendar,
            start,
            end,
            preferences,
            pref_version,
            account_version,
        )
        result = await response(session, runtime.owner, action.id)
        runtime.state["calendar_event_request"]["action_id"] = action.id
        runtime.state["last_calendar_action_id"] = action.id
        await store.checkpoint(
            session, runtime.owner, runtime.request, runtime.lease, runtime.state, result
        )
        return result


async def retire_candidate(runtime, pending, target, *, new_goal=False):
    if not pending or not pending.get("action_id"):
        return
    async with runtime.factory.begin() as session:
        # Match Calendar dispatch's account -> task -> action lock order.
        await session.get(User, runtime.owner, with_for_update=True)
        chat = await store.owned(session, runtime.owner, runtime.request.conversation_id, lock=True)
        if (
            chat.lease_id != runtime.lease
            or not chat.lease_until
            or chat.lease_until <= datetime.now(UTC)
        ):
            raise ApiError(
                409, "conversation_lease_lost", "Reload this conversation before continuing."
            )
        _, action = await actions.owned_action(
            session, runtime.owner, pending["action_id"], lock=True
        )
        if action.state == target:
            return
        if action.state not in {"proposed", "approved"}:
            if new_goal:
                return
            raise ApiError(
                409,
                "calendar_event_already_dispatched",
                "This event has already been dispatched or stopped. Check its status; "
                "I cannot edit it or create a replacement automatically.",
            )
        await actions.stop_before_dispatch(
            session, runtime.owner, action.id, expected_version=action.version, state=target
        )


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
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": event_timezone.zone_name(args.timezone)
            if args.timezone
            else preferences["timezone"],
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": event_timezone.zone_name(args.timezone)
            if args.timezone
            else preferences["timezone"],
        },
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
            "default_duration_minutes": preferences["default_duration_minutes"]
            if not args.duration_phrase
            else None,
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
    if action["state"] == "proposed":
        # view() has already verified ownership. Keep this explanation tied to
        # the immutable candidate, not to a possibly newer preference value.
        stored = await session.get(AssistantAction, action_id)
        if minutes := stored.source_versions.get("default_duration_minutes"):
            messages["proposed"] = (
                f"Using your saved {minutes}-minute duration. Review this event before creating it."
            )
    return {
        "kind": "calendar_event",
        "text": messages.get(action["state"], "Check the event status below."),
        "calendar_action": action,
        "calendar_action_id": action_id,
    }
