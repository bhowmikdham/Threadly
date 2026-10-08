"""An explicit email-card click and user-edited event fields, never model authority."""

from datetime import date as Date
from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.assistant import DraftOptions, StrictModel


class MeetingEmailSource(StrictModel):
    kind: Literal["gmail_message"]
    thread_id: str = Field(pattern=r"^[a-fA-F0-9]{1,32}$")
    message_id: str = Field(pattern=r"^[a-fA-F0-9]{1,32}$")


class PrepareMeetingEmail(StrictModel):
    source: MeetingEmailSource


class PreviewMeetingEmail(PrepareMeetingEmail):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    context_snapshot_id: UUID = Field(strict=False)
    expected_preferences_version: int = Field(strict=True, ge=1)
    calendar_id: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    date: Date = Field(strict=False)
    start_time: time = Field(strict=False)
    duration_minutes: int = Field(strict=True, ge=5, le=480)
    location: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=4000)
    attendees: list[str] = Field(default_factory=list, max_length=20)
    send_updates: Literal["all", "none"]

    @field_validator("context_snapshot_id", "date", "start_time", mode="before")
    @classmethod
    def string_fields(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use a string identifier, ISO date or local clock time.")
        return value

    @field_validator("attendees")
    @classmethod
    def addresses(cls, value):
        return DraftOptions(to=value).to

    @field_validator("start_time")
    @classmethod
    def local_clock(cls, value):
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("Use a local start time to the minute in the displayed timezone.")
        return value

    @model_validator(mode="after")
    def explicit_notifications(self):
        if self.attendees and self.send_updates != "all":
            raise ValueError("Confirm invitations for the listed attendees.")
        if self.calendar_id != self.calendar_id.strip() or any(
            ord(c) < 32 for c in self.calendar_id
        ):
            raise ValueError("Invalid calendar.")
        return self
