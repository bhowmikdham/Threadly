"""Explicit local-mail scope; natural language never supplies database predicates."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class MailSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    query: str = Field(min_length=1, max_length=200, pattern=r"\S")
    folder: Literal["all_mail", "all_synced", "INBOX", "SENT"]
    received_from: AwareDatetime
    received_before: AwareDatetime
    cursor: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("received_from", "received_before", mode="before")
    @classmethod
    def parse_dates(cls, value):
        # FastAPI validates a decoded dict; parse ISO strings before strict validation.
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    @field_validator("received_from", "received_before")
    @classmethod
    def utc_dates(cls, value):
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_window(self):
        duration = self.received_before - self.received_from
        if duration <= timedelta(0) or duration > timedelta(days=366):
            raise ValueError("Choose a positive date window of at most 366 days")
        return self
