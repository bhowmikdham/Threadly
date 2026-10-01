"""Read-only conversational Calendar tools; no provider identifiers or write grants."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.assistant import StrictModel


class ListCalendars(StrictModel):
    pass


class RelativeDate(StrictModel):
    kind: Literal["relative"]
    offset_days: int = Field(ge=-31, le=90, strict=True)
    days: int = Field(default=1, ge=1, le=14, strict=True)


class WeekdayDate(StrictModel):
    kind: Literal["weekday"]
    weekday: int = Field(ge=0, le=6, strict=True, description="Monday=0 through Sunday=6")
    week: Literal["upcoming", "this", "next"] = "upcoming"


class WeekDate(StrictModel):
    kind: Literal["week"]
    week: Literal["this", "next"]


class AbsoluteDate(StrictModel):
    kind: Literal["absolute"]
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(default="", pattern=r"^(?:\d{4}-\d{2}-\d{2})?$")


DateMeaning = Annotated[
    RelativeDate | WeekdayDate | WeekDate | AbsoluteDate, Field(discriminator="kind")
]


class CalendarWindow(StrictModel):
    subject: Literal["self", "other"] | None = Field(
        default=None,
        description=(
            "Whose calendar availability is requested? Use other for another named person; "
            "never substitute self."
        ),
    )
    date: DateMeaning | None = None
    date_phrase: str = Field(
        default="",
        max_length=100,
        description="Legacy literal-date calls only; use date for semantic interpretation",
    )
    date_source: str = Field(default="", max_length=100)
    start_time: str = Field(default="", max_length=20)
    end_time: str = Field(default="", max_length=20)
    start_time_source: str = Field(default="", max_length=40)
    end_time_source: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def paired_times(self):
        if bool(self.date) == bool(self.date_phrase):
            raise ValueError("Supply exactly one structured date or legacy date_phrase")
        if self.date is not None and self.subject is None:
            raise ValueError("Structured dates require an explicit self/other calendar subject")
        if self.date is not None and not self.date_source:
            raise ValueError("Structured dates require the original user date_source")
        if (self.start_time_source and not self.start_time) or (
            self.end_time_source and not self.end_time
        ):
            raise ValueError("Clock source quotes need their corresponding clock values")
        if bool(self.start_time) != bool(self.end_time):
            raise ValueError("Supply both ends of a time window")
        return self


class SearchCalendarEvents(CalendarWindow):
    query: str = Field(default="", max_length=200)


class FindFreeTimes(CalendarWindow):
    duration_phrase: str = Field(default="", max_length=40)
    limit: int = Field(default=3, strict=True, ge=1, le=3)


CALENDAR_READ_TOOLS = {
    "list_calendars": (
        ListCalendars,
        "List the connected user's accessible calendars. Does not change calendar selections.",
    ),
    "search_calendar_events": (
        SearchCalendarEvents,
        "Find existing events on selected calendars by a date window and optional literal "
        "search words copied from the user's request. Empty query lists all events. "
        "Returns a checked answer, including partial coverage and private-event redaction.",
    ),
    "find_busy_times": (
        CalendarWindow,
        "Read and merge busy intervals on the user's selected calendars for a date/time window. "
        "Never establishes another person's availability. Returns a checked answer.",
    ),
    "find_free_times": (
        FindFreeTimes,
        "Find up to three free meeting slots directly, without a proposal. Copy duration_phrase "
        "from the user (e.g. '30 minutes', 'half an hour'); empty uses saved default duration. "
        "Respects saved working hours, buffers and notice. No booking or participant availability.",
    ),
    "find_overlapping_events": (
        CalendarWindow,
        "Find events whose time ranges overlap on the user's selected calendars. "
        "Adjacent events do not overlap. Reports observed overlaps, not guaranteed conflicts "
        "or a complete result when Calendar coverage is partial.",
    ),
}

WINDOW_HELP = (
    " Set subject to self for the user's calendars or other for another person's "
    "availability. Other-person requests do not read the user's calendars. "
    "Interpret date wording semantically, using the structured date object (leave legacy "
    "date_phrase empty). For tomorrow: date={kind:'relative',offset_days:1}; day after "
    "tomorrow: offset_days:2; next seven days: offset_days:0,days:7. For Thursday next "
    "week: date={kind:'weekday',weekday:3,week:'next'}. For this/next calendar week use "
    "kind:'week',week:'this' or 'next'. Only explicit ISO dates use kind:'absolute', "
    "start:'YYYY-MM-DD', optional inclusive end. Put the exact user's date wording in "
    "date_source; this can be informal, misspelled or in another language. The backend "
    "computes actual dates using its saved clock and the user's Calendar timezone. "
    "Do not calculate an ISO date for relative wording, and do not ask for spelling "
    "corrections. date_source must not swallow clock or other constraints. Copy both "
    "start_time_source/end_time_source exactly from the user and supply normalized "
    "start_time/end_time (e.g. source '2 pm', value '14:00'). Clock windows require "
    "a single day and explicit "
    "AM/PM or 24-hour HH:mm. Preserve every constraint; clarify genuine ambiguity. "
    "Call alone for a read; compound work uses prepare_workflow."
)

for _name, (_schema, _description) in list(CALENDAR_READ_TOOLS.items()):
    CALENDAR_READ_TOOLS[_name] = (
        _schema,
        _description + (WINDOW_HELP if _name != "list_calendars" else " Terminal read."),
    )
