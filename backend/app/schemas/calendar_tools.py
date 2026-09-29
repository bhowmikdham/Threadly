"""Read-only conversational Calendar tools; no provider identifiers or write grants."""

from pydantic import Field, model_validator

from app.schemas.assistant import StrictModel


class ListCalendars(StrictModel):
    pass


class CalendarWindow(StrictModel):
    date_phrase: str = Field(min_length=1, max_length=100)
    start_time: str = Field(default="", max_length=20)
    end_time: str = Field(default="", max_length=20)

    @model_validator(mode="after")
    def paired_times(self):
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
    " Copy date_phrase exactly from the user's words: today, tomorrow, a weekday "
    "(optionally this/next or this/next week), this week, next week, next 7 days, "
    "YYYY-MM-DD, or YYYY-MM-DD to YYYY-MM-DD (inclusive; at most 14 days). "
    "Copy both start_time and end_time if given; use explicit AM/PM or 24-hour HH:mm. "
    "Time windows apply to a single day. Do not omit date/time constraints. "
    "Unsupported or ambiguous times require clarification. All tools are terminal: "
    "call alone to answer a read request. Compound work still uses prepare_workflow."
)
for _name, (_schema, _description) in list(CALENDAR_READ_TOOLS.items()):
    CALENDAR_READ_TOOLS[_name] = (
        _schema,
        _description + (WINDOW_HELP if _name != "list_calendars" else " Terminal read."),
    )
