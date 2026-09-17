"""User-supplied event details; times are loaded only from an owned selected slot."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.assistant import DraftOptions, StrictModel


class ProposeCalendarAction(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    selection_id: str = Field(min_length=1, max_length=36)
    calendar_id: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    description: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=500)
    attendees: list[str] = Field(default_factory=list, max_length=20)
    send_updates: Literal["all", "none"]

    @field_validator("attendees")
    @classmethod
    def mailboxes(cls, value):
        return DraftOptions(to=value).to

    @model_validator(mode="after")
    def notifications(self):
        if self.attendees and self.send_updates != "all":
            raise ValueError("Invitations require explicit all-attendee notifications")
        if self.calendar_id != self.calendar_id.strip() or any(
            ord(c) < 32 for c in self.calendar_id
        ):
            raise ValueError("Invalid destination calendar")
        return self
