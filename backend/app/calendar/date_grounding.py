"""Check explicit date constraints against resolved local dates, independent of tool kind.

This is a consistency fence, not an intent classifier or a replacement language parser.
Unrecognized wording retains the semantic tool path. Recognized weekdays, ISO dates and
relative-day phrases cannot contradict the resolved result, including canonicalized drafts.
"""

import re
from datetime import date, timedelta

from app.calendar.day_availability import WEEKDAYS
from app.calendar.event_draft import FieldRepairRequired

WEEKDAY = "|".join(WEEKDAYS)


def validate(source, first, last, today):
    """Validate one event day using the same saved local anchor as date resolution."""
    phrase = " ".join(source.casefold().split()).strip(" .!?,")
    phrase = re.sub(r"^(?:on\s+)?(?:the\s+)?", "", phrase)

    def require(condition):
        if not condition:
            raise FieldRepairRequired("date", interpretation=True)

    weekdays = {WEEKDAYS.index(day) for day in re.findall(rf"\b({WEEKDAY})\b", phrase)}
    if weekdays:
        require(len(weekdays) == 1 and first.weekday() in weekdays)
        require(last == first + timedelta(days=1))

    # An explicit date and a weekday constrain the same day independently. This
    # catches contradictory source wording as well as incorrect model extraction.
    stated = re.findall(r"(?<!\w)\d{4}-\d{2}-\d{2}(?!\w)", phrase)
    if stated:
        try:
            dates = {date.fromisoformat(value) for value in stated}
        except ValueError:
            require(False)
        require(dates == {first} and last == first + timedelta(days=1))
    weekday = re.search(rf"\b(?:(this|next) )?({WEEKDAY})(?: (this|next) week)?\b", phrase)
    if weekday and (weekday[0] == phrase or (stated and (weekday[1] or weekday[3]))):
        qualifiers = {value for value in (weekday[1], weekday[3]) if value}
        require(len(qualifiers) <= 1)
        offset = WEEKDAYS.index(weekday[2]) - today.weekday()
        if "next" in qualifiers:
            offset += 7
        elif "this" not in qualifiers:
            offset %= 7
        require(first == today + timedelta(days=offset))
        return

    offsets = {"today": 0, "tomorrow": 1, "tmrw": 1, "tmr": 1, "day after tomorrow": 2}
    offset = offsets.get(phrase)
    if relative := re.fullmatch(r"in (\d{1,2}) days?", phrase):
        offset = int(relative[1])
    if offset is not None:
        require(first == today + timedelta(days=offset))
        require(last == first + timedelta(days=1))
