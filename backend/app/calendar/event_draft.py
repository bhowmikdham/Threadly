"""Field-level event memory. Models propose changes; user source and state bind them."""

import re
from datetime import UTC, datetime, timedelta

from app.calendar.conversation_tools import RequestClarification, literal

FIELDS = (
    "title",
    "date",
    "time",
    "duration_phrase",
    "calendar_name",
    "location",
    "description",
    "attendees",
)
CONTROLS = {"intent", "changes", "continue_previous"}
SOURCE_FIELDS = {"date": "date_source", "time": "time_source"}


class IntentSourceMismatch(ValueError):
    """The model must repair its source quote before any draft state changes."""


def evidence(arguments):
    values = [arguments.get(SOURCE_FIELDS.get(field, field)) for field in FIELDS]
    return "\n".join(
        str(item)
        for value in values
        if value
        for item in (value if isinstance(value, list) else [value])
    )


def amendment_directive(text, sources):
    # The model chooses the field, but quoted/reporting text is not an instruction
    # to amend a draft. Bare literal answers are allowed; commands need a direct
    # request prefix. Data inside a quoted title cannot supply a removal command.
    unquoted = re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', " ", text)
    words = re.findall(r"[\w']+", unquoted.casefold())
    operations = {
        "change",
        "set",
        "make",
        "use",
        "rename",
        "move",
        "reschedule",
        "replace",
        "clear",
        "remove",
        "delete",
        "drop",
        "without",
        "no",
    }
    prefixes = {
        "hi",
        "hello",
        "hey",
        "please",
        "kindly",
        "can",
        "could",
        "would",
        "will",
        "you",
        "i",
        "want",
        "need",
        "like",
        "to",
        "help",
        "me",
        "actually",
        "instead",
        "then",
        "and",
        "also",
    }
    for index, word in enumerate(words):
        if word in operations:
            return all(item in prefixes for item in words[:index])
    return any(
        text.strip().casefold().rstrip(".! ") == str(source).strip().casefold()
        for source in sources
        if isinstance(source, str)
    )


def merge(runtime, args, pending):
    from app.calendar.event_creation import source_fields, user_directive
    from app.schemas.conversation import PrepareCalendarEvent

    latest = user_directive(runtime.request.instruction)
    if args.intent:
        if " ".join(args.intent.source.split()) != " ".join(latest.split()):
            raise IntentSourceMismatch
    if pending and re.match(
        r"\s*(?:summari[sz]e|translate|quote|read|explain|draft|reply|search)\b", latest, re.I
    ):
        raise RequestClarification(
            "Please give event changes directly, outside email or quoted text."
        )
    old = {k: v for k, v in (pending or {}).get("arguments", {}).items() if k not in CONTROLS}
    values = dict(old)
    supplied = {
        k: v
        for k, v in args.model_dump(mode="json").items()
        if k not in CONTROLS and v not in (None, "", []) and v != old.get(k)
    }
    for field, source in SOURCE_FIELDS.items():
        if field in supplied or source in supplied:
            supplied[field] = args.model_dump(mode="json")[field]
            supplied[source] = getattr(args, source)
    changes = {}
    for field in FIELDS:
        if field in supplied:
            changes[field] = {
                "operation": "replace",
                "source": supplied.get(SOURCE_FIELDS.get(field, field), ""),
                "request_id": runtime.request.request_id,
            }
    for change in args.changes:
        field = change.field
        if field in supplied:
            raise ValueError("Use either a field change or a legacy value for each field")
        literal(change.source, latest)
        if change.operation in {"clear", "remove"}:
            literal(change.source, re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', " ", latest))
            if not re.search(r"\b(?:clear|remove|delete|drop|without|no)\b", change.source, re.I):
                raise RequestClarification("Please explicitly say which event field to clear.")
            names = {
                "attendees": r"guests?|attendees?|invitees?",
                "location": r"location|place|room",
                "date": r"date|day",
                "time": r"time",
                "title": r"title|name",
                "description": r"description|notes?",
                "calendar_name": r"calendar",
                "duration_phrase": r"duration|length",
            }
            old_values = old.get(field) or []
            if not isinstance(old_values, list):
                old_values = [old_values] if isinstance(old_values, str) else []
            if not re.search(r"\b(?:" + names[field] + r")\b", change.source, re.I) and not any(
                str(v).casefold() in change.source.casefold() for v in old_values
            ):
                raise RequestClarification("Which event field should I clear?")
        if change.operation == "remove":
            previous = old.get("attendees", [])
            for address in change.value:
                literal(address, change.source)
                if address not in previous:
                    raise RequestClarification("That guest is not in the pending event.")
            supplied[field] = [v for v in previous if v not in change.value]
        elif change.operation == "clear":
            supplied[field] = [] if field == "attendees" else None if field == "date" else ""
            if field in SOURCE_FIELDS:
                supplied[SOURCE_FIELDS[field]] = ""
        else:
            value = (
                change.value.model_dump(mode="json")
                if hasattr(change.value, "model_dump")
                else change.value
            )
            supplied[field] = value
            if field in SOURCE_FIELDS:
                supplied[SOURCE_FIELDS[field]] = change.source
            if field not in SOURCE_FIELDS:
                for entry in value if isinstance(value, list) else [value]:
                    literal(entry, change.source)
        changes[field] = {
            "operation": change.operation,
            "source": change.source,
            "request_id": runtime.request.request_id,
        }
    if pending and supplied.get("calendar_name"):
        from app.calendar.event_choices import explicit_selection

        if not explicit_selection(supplied["calendar_name"], latest):
            raise RequestClarification("Choose a calendar from the list, or tell me its name.")
    corrected = {field for field in changes if old.get(field)}
    if (
        pending
        and corrected.difference({"calendar_name"})
        and not amendment_directive(latest, [item["source"] for item in changes.values()])
    ):
        raise RequestClarification(
            "Please give the event correction directly, outside quoted text."
        )
    values.update(supplied)
    candidate = PrepareCalendarEvent.model_validate(values)
    # Validate the latest delta against this turn only. Retained values are already
    # source-bound. Replacing a date cannot leave the old date as a live constraint.
    check = dict(supplied)
    for change in args.changes:
        if change.operation == "remove":
            check.pop("attendees", None)
    source_fields(
        PrepareCalendarEvent.model_validate(check),
        latest,
        evidence(old).split("\n") if pending else [],
    )
    provenance = dict((pending or {}).get("field_provenance", {}))
    if pending and not provenance:
        # Legacy structured drafts may be resumed, but never reconstructed from
        # assistant prose or historical provider observations.
        provenance = {
            field: {
                "request_id": pending.get("last_request_id"),
                "source": old.get(SOURCE_FIELDS.get(field, field)),
                "operation": "replace",
            }
            for field in FIELDS
            if old.get(field)
        }
    provenance.update(changes)
    anchor = (
        runtime.calendar_anchor
        if "date" in changes or not pending
        else datetime.fromisoformat(pending["anchor"])
    )
    saved = {
        "schema_version": 2,
        "goal_id": (pending or {}).get("goal_id", runtime.request.request_id),
        "revision": (pending or {}).get("revision", 0) + bool(changes),
        "arguments": candidate.model_dump(mode="json", exclude=CONTROLS),
        "field_provenance": provenance,
        "user_text": evidence(candidate.model_dump()),
        "anchor": anchor.isoformat(),
        "expires_at": (pending or {}).get("expires_at")
        or (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        "last_request_id": runtime.request.request_id,
    }
    if pending:
        for key in ("creation_origin", "action_id", "previous_calendar_names"):
            if key in pending:
                saved[key] = pending[key]
        # A destination-only reply must resolve against the displayed option order.
        # Other changed details make all old choice handles stale.
        corrected = {field for field in changes if old.get(field)}
        if not corrected.difference({"calendar_name"}):
            for key in ("calendar_choices", "selected_calendar"):
                if key in pending:
                    saved[key] = pending[key]
    return candidate, saved, bool(changes)
