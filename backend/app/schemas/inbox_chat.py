"""Read-only conversational inbox discovery; no model-supplied provider operators."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.mail_search import MailSearchRequest


class InboxChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    timezone: str = Field(default="UTC", max_length=80)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Choose a valid timezone") from None
        return value


class InboxFilters(MailSearchRequest):
    query: str = Field(default="", max_length=200)
    folder: Literal["all_mail", "INBOX", "SENT"] = "all_mail"
    cursor: None = None


class InboxPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filters: InboxFilters
    cursor: str = Field(min_length=1, max_length=4000)
