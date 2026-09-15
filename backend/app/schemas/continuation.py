"""Typed clarification input. No free-form command, tool name or approval field."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.schemas.assistant import DraftOptions, StrictModel


class ClarificationAnswer(StrictModel):
    context_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    recipients: list[str] | None = Field(default=None, min_length=1, max_length=20)
    reply_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    date_phrase: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    time_phrase: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"\S")
    am_or_pm: Literal["AM", "PM"] | None = None

    @field_validator("recipients")
    @classmethod
    def literal_recipients(cls, value):
        return DraftOptions(to=value).to if value is not None else None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Use a valid IANA timezone") from None
        return value

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Supply at least one typed answer")
        return self


class TaskInputRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    question_id: str = Field(min_length=1, max_length=36)
    answer: ClarificationAnswer
