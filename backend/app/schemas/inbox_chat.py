"""Read-only conversational inbox discovery; no model-supplied provider operators."""

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.mail_search import MailSearchRequest

EMAIL_ADDRESS = (
    r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}"
)


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
    sender_email: str = Field(default="", max_length=254)
    folder: Literal["all_mail", "INBOX", "SENT"] = "all_mail"
    limit: int = Field(default=5, ge=1, le=5)
    cursor: None = None

    @field_validator("sender_email")
    @classmethod
    def exact_sender_address(cls, value):
        if value and not re.fullmatch(EMAIL_ADDRESS, value):
            raise ValueError("Choose one exact sender email address")
        return value.casefold()


class InboxPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filters: InboxFilters
    cursor: str = Field(min_length=1, max_length=4000)
