"""Field-level event memory. Models propose changes; user source and state bind them."""

import re
from datetime import UTC, datetime, timedelta

from app.calendar.conversation_tools import RequestClarification, literal

FIELDS = (
    "title",
    "date",
    "time",
    "timezone",
    "duration_phrase",
    "calendar_name",
    "location",
    "description",
    "attendees",
)
CONTROLS = {"intent", "changes", "continue_previous", "citations", "email_source"}
SOURCE_FIELDS = {"date": "date_source", "time": "time_source", "timezone": "timezone_source"}


class IntentSourceMismatch(ValueError):
    """The model must repair its source quote before any draft state changes."""


class IncompleteEventTitle(ValueError):
    """An unambiguous trailing title was shortened by the model."""

    def __init__(self, title):
        self.title = title
        super().__init__("Preserve the complete user-supplied event title")


class FieldRepairRequired(ValueError):
    """Safe model feedback; never retain rejected values or source text in a trace."""

    def __init__(self, field, *, interpretation=False):
        self.field = field if field in FIELDS else "event"
        self.code = (
            "calendar_field_interpretation_mismatch"
            if interpretation
            else "calendar_field_source_mismatch"
        )
        super().__init__(self.code)


def literal_field(value, text, field):
    try:
        return literal(value, text)
    except RequestClarification:
        raise FieldRepairRequired(field) from None


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
    cited = getattr(runtime, "calendar_field_citations", {})
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
    repeated_sources = []
    if pending and args.intent and args.intent.operation == "resume":
        for field in FIELDS:
            value = getattr(args, field)
            if (
                isinstance(value, str)
                and value
                and value.strip().casefold() == str(old.get(field, "")).strip().casefold()
            ):
                source = SOURCE_FIELDS.get(field, field)
                subset = {field: value}
                if source != field:
                    subset[source] = getattr(args, source)
                prior = pending.get("field_provenance", {}).get(field, {})
                if not (field == "title" and prior.get("kind") == "email" and prior.get("derived")):
                    source_fields(
                        PrepareCalendarEvent.model_validate(subset),
                        cited.get(field, {}).get("source", latest),
                        constraints=False,
                    )
                repeated_sources.append(getattr(args, source))
                supplied.pop(field, None)
                supplied.pop(source, None)
    for field, source in SOURCE_FIELDS.items():
        if getattr(args, field) and getattr(args, field) == old.get(field):
            # Repeating the same interpreted field with different casing or a
            # shorter source quote is not a correction to a reviewed candidate.
            supplied.pop(source, None)
        if field in supplied or source in supplied:
            supplied[field] = args.model_dump(mode="json")[field]
            supplied[source] = getattr(args, source)
    changes = {}
    if (
        pending
        and args.intent
        and args.intent.operation == "resume"
        and any(old.get(field) and field in supplied for field in FIELDS)
    ):
        from app.calendar.conversation_guard import CalendarNewGoalRequired

        # Resume fills missing fields; it cannot silently replace a reviewed goal.
        # Semantic revisions use explicit changes. A new creation starts fresh.
        raise CalendarNewGoalRequired
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
        literal_field(change.source, latest, field)
        if field in cited and change.operation != "replace":
            raise ValueError("Historical data can replace a field, not authorize its removal")
        if change.operation in {"clear", "remove"}:
            literal_field(
                change.source, re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', " ", latest), field
            )
            if not re.search(r"\b(?:clear|remove|delete|drop|without|no)\b", change.source, re.I):
                raise RequestClarification("Please explicitly say which event field to clear.")
            names = {
                "attendees": r"guests?|attendees?|invitees?",
                "location": r"location|place|room",
                "date": r"date|day",
                "time": r"time",
                "timezone": r"timezone|time zone",
                "title": r"title|name",
                "description": r"description|notes?",
                "calendar_name": r"calendar",
                "duration_phrase": r"duration|length",
            }
            old_values = old.get(field) or []
            if not isinstance(old_values, list):
                old_values = [old_values] if isinstance(old_values, str) else []
            if field in SOURCE_FIELDS and (old_source := old.get(SOURCE_FIELDS[field])):
                old_values = [*old_values, old_source]
            if not re.search(r"\b(?:" + names[field] + r")\b", change.source, re.I) and not any(
                str(v).casefold() in change.source.casefold() for v in old_values
            ):
                raise RequestClarification("Which event field should I clear?")
        if change.operation == "remove":
            previous = old.get("attendees", [])
            for address in change.value:
                literal_field(address, change.source, field)
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
                supplied[SOURCE_FIELDS[field]] = cited.get(field, {}).get("source", change.source)
            if field not in SOURCE_FIELDS:
                for entry in value if isinstance(value, list) else [value]:
                    literal_field(entry, cited.get(field, {}).get("source", change.source), field)
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
    validation_text = latest
    if pending and pending.get("origin_pending_validation"):
        original = pending["creation_origin"]["text"]
        for field, source in pending.get("unresolved_sources", {}).items():
            if field in supplied and source:
                original = original.replace(source, " ")
        validation_text = original + "\n" + latest + "\n" + evidence(old)
        check = candidate.model_dump(mode="json", exclude=CONTROLS)
    for field, citation in cited.items():
        if not values.get(field):
            raise ValueError("A historical citation needs its corresponding proposed field")
        source = SOURCE_FIELDS.get(field, field)
        subset = {field: values[field]}
        if source != field:
            subset[source] = values[source]
        if not (field == "title" and citation.get("kind") == "email" and citation.get("derived")):
            source_fields(
                PrepareCalendarEvent.model_validate(subset), citation["source"], constraints=False
            )
        check.pop(field, None)
        if source != field:
            check.pop(source, None)
        if field in changes:
            changes[field].update(citation)
    cleared_zone_labels = (
        ["timezone", "time zone"]
        if any(
            change.field == "timezone" and change.operation == "clear" for change in args.changes
        )
        else []
    )
    source_fields(
        PrepareCalendarEvent.model_validate(check),
        validation_text,
        (evidence(old).split("\n") if pending else []) + repeated_sources + cleared_zone_labels,
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
    date_timezone = (pending or {}).get("date_anchor_timezone")
    if "date" in changes or not pending:
        date_timezone = None
        if date_citation := cited.get("date"):
            if candidate.date.kind != "absolute" and (
                not date_citation["recorded_at"] or not date_citation["timezone"]
            ):
                raise RequestClarification(
                    "Which calendar date do you mean? That older message has no saved local date."
                )
            if date_citation["recorded_at"]:
                anchor = datetime.fromisoformat(date_citation["recorded_at"])
                date_timezone = candidate.timezone or date_citation["timezone"]
    saved = {
        "schema_version": 2,
        "goal_id": (pending or {}).get("goal_id", runtime.request.request_id),
        "revision": (pending or {}).get("revision", 0) + bool(changes),
        "arguments": candidate.model_dump(mode="json", exclude=CONTROLS),
        "field_provenance": provenance,
        "user_text": evidence(candidate.model_dump()),
        "anchor": anchor.isoformat(),
        "date_anchor_timezone": date_timezone,
        "expires_at": (pending or {}).get("expires_at")
        or (datetime.now(UTC) + timedelta(days=7)).isoformat(),
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


def retain_partial(runtime, args, pending, origin, error):
    """Keep independently grounded fields after clarification, never an approval.

    An invalid field cannot erase the other supplied details. Reviewed candidates are
    unchanged until a complete validated revision can retire them atomically.
    """
    from app.calendar.event_creation import source_fields
    from app.schemas.conversation import PrepareCalendarEvent

    current = runtime.state.get("calendar_event_request") or {}
    if current.get("action_id") or args.changes or isinstance(error, IntentSourceMismatch):
        return
    values = dict((pending or {}).get("arguments", {}))
    provenance = dict((pending or {}).get("field_provenance", {}))
    unresolved = dict((pending or {}).get("unresolved_sources", {}))
    supplied = args.model_dump(mode="json", exclude=CONTROLS)
    cited = getattr(runtime, "calendar_field_citations", {})
    for field in FIELDS:
        if not supplied.get(field):
            continue
        source = SOURCE_FIELDS.get(field, field)
        subset = {field: supplied[field]}
        if source != field:
            subset[source] = supplied[source]
        try:
            if isinstance(error, IncompleteEventTitle) and field == "title":
                raise error
            if isinstance(error, FieldRepairRequired) and field == error.field:
                raise error
            if (
                field == "date"
                and field in cited
                and args.date.kind != "absolute"
                and not (cited[field]["recorded_at"] and cited[field]["timezone"])
            ):
                raise RequestClarification("That old relative date has no saved clock")
            citation = cited.get(field, {})
            if not (
                field == "title" and citation.get("kind") == "email" and citation.get("derived")
            ):
                source_fields(
                    PrepareCalendarEvent.model_validate(subset),
                    citation.get("source", runtime.request.instruction),
                    constraints=False,
                )
        except (ValueError, RequestClarification):
            rejected_source = supplied.get(source, "")
            if (
                field in SOURCE_FIELDS
                and isinstance(rejected_source, str)
                and rejected_source.casefold() in runtime.request.instruction.casefold()
            ):
                unresolved[field] = rejected_source
            continue
        if values.get(field) and values[field] != supplied[field]:
            continue
        values.update(subset)
        provenance[field] = {
            "operation": "replace",
            "source": supplied.get(source),
            "request_id": runtime.request.request_id,
            **cited.get(field, {}),
        }
        unresolved.pop(field, None)
    candidate = PrepareCalendarEvent.model_validate(values)
    dated = provenance.get("date", {})
    runtime.state["calendar_event_request"] = {
        "schema_version": 2,
        "goal_id": (pending or {}).get("goal_id", runtime.request.request_id),
        "revision": (pending or {}).get("revision", 0),
        "arguments": candidate.model_dump(mode="json", exclude=CONTROLS),
        "field_provenance": provenance,
        "user_text": evidence(candidate.model_dump()),
        "creation_origin": origin,
        "origin_pending_validation": True,
        "unresolved_sources": unresolved,
        "anchor": dated.get("recorded_at")
        or (pending or {}).get("anchor", runtime.calendar_anchor.isoformat()),
        "date_anchor_timezone": dated.get("timezone")
        or (pending or {}).get("date_anchor_timezone"),
        "expires_at": (pending or {}).get("expires_at")
        or (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "last_request_id": runtime.request.request_id,
    }
