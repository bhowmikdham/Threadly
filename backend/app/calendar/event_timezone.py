"""Bind explicit event zones without treating Australian standard time as daylight time."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.calendar.conversation_tools import RequestClarification

ALIASES = {"AEST": "Etc/GMT-10", "AEDT": "Etc/GMT-11", "UTC": "UTC", "GMT": "UTC"}
ZONE = r"(?:AEST|AEDT|UTC|GMT|[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)"
CLOCK = r"\d{1,2}(?::[0-5]\d)?\s*(?:[ap]\.?\s*m\.?)?"


def zone_name(value):
    name = ALIASES.get(value.upper(), value)
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise RequestClarification(
            "Which timezone should I use? Please give a city or IANA timezone."
        ) from None
    return name


def clock_source(value):
    match = re.fullmatch(rf"({CLOCK})\s+({ZONE})", value.strip(), re.I)
    return match[1].strip() if match else value


def bind(args, text):
    """Preserve an explicit, clock-attached source even if extraction omitted its zone."""
    from app.calendar.event_draft import FieldRepairRequired, literal_field

    explicit = list(re.finditer(rf"(?<!\w)({CLOCK})\s+({ZONE})\b", text, re.I))
    # An unfamiliar clock-attached abbreviation cannot silently fall back to the
    # user's default zone when the model quotes just the clock. Only uppercase
    # abbreviations or explicit offsets are examined, not ordinary following prose.
    unknown = re.search(
        rf"(?<!\w)((?i:{CLOCK}))\s+((?:UTC|GMT)?[+-]\d{{1,2}}(?::?\d{{2}})?|[A-Z]{{2,5}})\b",
        text,
    )
    if unknown and unknown[2] not in ALIASES and unknown[2] not in {"AM", "PM"}:
        raise RequestClarification(
            "Which timezone should I use? Please give an IANA timezone, AEST or AEDT."
        )
    # The source of a timezone must be current user wording, not browser location.
    if args.timezone:
        literal_field(args.timezone_source, text, "timezone")
        if zone_name(args.timezone) != zone_name(args.timezone_source):
            raise FieldRepairRequired("timezone", interpretation=True)
    if explicit:
        names = {zone_name(match[2]) for match in explicit}
        if len(names) != 1:
            raise RequestClarification("Which of those timezones should I use for this event?")
        source, name = explicit[0][2], names.pop()
        if args.timezone and zone_name(args.timezone) != name:
            raise FieldRepairRequired("timezone", interpretation=True)
        if not args.timezone:
            args = args.model_copy(update={"timezone": name, "timezone_source": source})
    return args
