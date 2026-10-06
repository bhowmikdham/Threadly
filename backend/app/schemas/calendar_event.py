"""Semantic draft transitions, separate from Calendar execution authority."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import DraftOptions, StrictModel
from app.schemas.calendar_tools import DateMeaning


class CalendarIntent(StrictModel):
    operation: Literal["create", "resume", "revise", "cancel"]
    source: str = Field(min_length=1, max_length=6000)


class EventFieldChange(StrictModel):
    field: Literal[
        "title",
        "date",
        "time",
        "duration_phrase",
        "calendar_name",
        "location",
        "description",
        "attendees",
    ]
    operation: Literal["replace", "clear", "remove"]
    source: str = Field(min_length=1, max_length=500)
    value: DateMeaning | str | list[str] | None = None

    @model_validator(mode="after")
    def typed_operation(self):
        if self.operation == "clear":
            if self.value is not None:
                raise ValueError("Clear has no replacement value")
        elif self.field == "attendees":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("Supply explicit guest email addresses")
            self.value = DraftOptions(to=self.value).to
        elif self.operation == "remove":
            raise ValueError("Remove addresses from attendees; use clear for other fields")
        elif self.field == "date":
            if self.value is None or isinstance(self.value, (str, list)):
                raise ValueError("A date replacement needs a structured date")
        elif not isinstance(self.value, str) or not self.value:
            raise ValueError("A replacement needs a nonempty value")
        return self
