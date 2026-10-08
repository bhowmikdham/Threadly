"""Owner-bound email evidence for an explicitly requested, separately approved event.

Only current read excerpts can establish new fields. Durable state keeps a source
reference and selected event facts, never an email body or an instruction from it.
"""

import re
from datetime import date, datetime

from sqlalchemy import select

from app.api.errors import ApiError
from app.assistant import source_data
from app.assistant.summary import digest
from app.calendar.conversation_tools import RequestClarification
from app.calendar.event_draft import FieldRepairRequired
from app.db.models import ContextSnapshot


def normalized(value):
    return " ".join(value.split())


def clock_context(observed, quote):
    """A quotation boundary cannot cut a clock off from its provider qualifier."""
    from app.calendar.event_timezone import CLOCK, ZONE

    text, excerpt = normalized(observed), normalized(quote)
    start = text.index(excerpt)
    end = start + len(excerpt)
    qualifier = rf"(?i:{ZONE})|(?:UTC|GMT)?[+-]\d{{1,2}}(?::?\d{{2}})?|[A-Z]{{2,5}}"
    for match in re.finditer(rf"(?<!\w)(?i:{CLOCK})\s+(?:{qualifier})\b", text):
        if start <= match.start() < end < match.end():
            end = match.end()
    return text[start:end]


def absolute_date(source):
    """Do not choose between ambiguous numeric dates or invent an omitted year."""
    value = source.strip().rstrip(".,")
    values = set()
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
    ):
        try:
            values.add(datetime.strptime(value, fmt).date())
        except ValueError:
            pass
    if len(values) != 1:
        raise RequestClarification(
            "Which calendar date does the email mean? Its date is ambiguous."
        )
    return values.pop()


async def refresh(runtime, binding):
    """Fresh reads happen before database checks/locks, including every continuation."""
    async with runtime.factory() as session:
        capture = await session.scalar(
            select(ContextSnapshot).where(
                ContextSnapshot.id == binding["context_snapshot_id"],
                ContextSnapshot.user_id == runtime.owner,
            )
        )
        if not capture:
            raise ApiError(404, "context_not_found", "Select an accessible email.")
        ref = capture.payload
    await source_data.prefetch(runtime.owner, [ref])
    async with runtime.factory() as session:
        from app.calendar.meeting_email import owned_source

        await owned_source(
            session, runtime.owner, binding["context_snapshot_id"], binding["source"]
        )


async def resolve(runtime, args, pending):
    previous = (pending or {}).get("email_source")
    if previous:
        await refresh(runtime, previous)
    envelope = args.email_source
    if envelope is None:
        return args, {}, previous, None, None
    if not normalized(envelope.event_quote):
        raise ValueError("Event evidence cannot be blank")
    reference = envelope.reference
    ref = runtime.state["refs"].get(reference)
    source = runtime.loaded.get(reference)
    observations = getattr(runtime, "email_observations", {}).get(reference, [])
    if not ref or not source or source.get("owner") != runtime.owner or not observations:
        raise ValueError("Read the owned email reference this turn before citing its event fields")
    matches = [
        item
        for item in observations
        if normalized(envelope.event_quote) in normalized(item["text"])
    ]
    if len(matches) != 1:
        raise RequestClarification("Which email contains the event you want to create?")
    if normalized(matches[0]["text"]).count(normalized(envelope.event_quote)) != 1:
        raise RequestClarification("Which event in this email should I use?")
    event_text = clock_context(matches[0]["text"], envelope.event_quote)
    question = {
        "multiple_events": "Which event from the email should I use?",
        "date": "Which date from the email should I use?",
        "time": "Which start time from the email should I use?",
    }.get(envelope.ambiguity)
    omitted = set()
    if envelope.ambiguity == "multiple_events":
        omitted.update(item.field for item in envelope.fields)
    elif question:
        omitted.add(envelope.ambiguity)
    message_id = matches[0]["message_id"]
    identity = {"kind": "gmail_message", "thread_id": ref["thread_id"], "message_id": message_id}
    if previous and previous["source"] != identity:
        raise ValueError(
            "A retained event cannot silently switch its source email; start a new goal"
        )
    citations = {}
    repair = None
    user_fields = {item.field for item in args.citations} | {item.field for item in args.changes}
    for item in envelope.fields:
        if item.field in user_fields:
            raise ValueError("User corrections take precedence; do not cite email for that field")
        if not normalized(item.quote) or normalized(item.quote) not in normalized(
            envelope.event_quote
        ):
            repair = repair or FieldRepairRequired(item.field)
            omitted.add(item.field)
            continue
        value = getattr(args, item.field)
        if not value:
            raise ValueError("Email evidence needs its corresponding event field")
        if item.field in omitted:
            continue
        if item.field == "date":
            try:
                stated = absolute_date(args.date_source)
            except RequestClarification as error:
                omitted.add("date")
                question = str(error)
                continue
            if args.date.kind != "absolute" or date.fromisoformat(args.date.start) != stated:
                repair = repair or FieldRepairRequired("date", interpretation=True)
                omitted.add("date")
                continue
        citations[item.field] = {
            "kind": "email",
            "source": item.quote,
            "quote_hash": digest(item.quote),
            "derived": item.field == "title",
            "recorded_at": None,
            "timezone": None,
        }
    # Inspect the complete verified event excerpt, not only model-selected short
    # field quotes. Otherwise quoting "8:30 AM" could silently drop its AEST.
    user_zone = (
        "timezone" in user_fields
        or (args.timezone_source and args.timezone_source in runtime.request.instruction)
        or (
            (pending or {}).get("arguments", {}).get("timezone")
            and (pending or {}).get("field_provenance", {}).get("timezone", {}).get("kind")
            != "email"
        )
    )
    timezone_required = False
    if not user_zone and envelope.ambiguity != "multiple_events":
        from app.calendar import event_timezone

        try:
            args = event_timezone.bind(args, event_text)
        except FieldRepairRequired as error:
            repair = repair or error
            omitted.add("timezone")
            timezone_required = True
        except RequestClarification as error:
            question = str(error)
            omitted.add("timezone")
            timezone_required = True
        else:
            if args.timezone:
                citations["timezone"] = {
                    "kind": "email",
                    "source": event_text,
                    "quote_hash": digest(event_text),
                    "derived": False,
                    "recorded_at": None,
                    "timezone": None,
                }
    for field in omitted:
        citations.pop(field, None)
    async with runtime.factory.begin() as session:
        capture = await source_data.capture(
            session, runtime.owner, ref["thread_id"], message_id=message_id
        )
    binding = {
        "context_snapshot_id": capture.id,
        "source": identity,
        "reference": reference,
        "fingerprint": source["fingerprint"],
        "timezone_required": timezone_required,
    }
    for citation in citations.values():
        citation["context_snapshot_id"] = capture.id
    changes = {field: None if field == "date" else "" for field in omitted}
    for field in ("date", "time", "timezone"):
        if field in omitted:
            changes[field + "_source"] = ""
    return args.model_copy(update=changes), citations, binding, question, repair


def persist(saved, binding):
    if binding:
        saved["email_source"] = binding
    for provenance in saved.get("field_provenance", {}).values():
        if provenance.get("kind") == "email":
            # Exact excerpts exist only in the current read. Keep integrity/provenance
            # handles, while the event's date/time/location remain ordinary draft facts.
            provenance.pop("source", None)
            provenance.pop("recorded_at", None)
            provenance.pop("timezone", None)
