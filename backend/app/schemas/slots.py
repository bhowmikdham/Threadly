"""Typed scheduling inputs. Natural-language extraction is a separate, untrusted layer."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.schemas.calendar import StrictModel

SLOT_POLICY = "calendar-slots-1.0.0"
STEP_MINUTES = 15


def valid_zone(value):
    if value is None:
        return value
    if value in {"localtime", "posixrules"} or value.startswith(("posix/", "right/")):
        raise ValueError("Use a named IANA timezone.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Use a named IANA timezone.") from None
    return value


class TimeContext(StrictModel):
    """User-supplied local clock window; cannot be inferred from busy/free results."""

    start_minute: int = Field(strict=True, ge=0, le=1439)
    end_minute: int = Field(strict=True, ge=1, le=1440)

    @model_validator(mode="after")
    def ordered(self):
        if self.start_minute >= self.end_minute:
            raise ValueError("Time context must have ordered bounds within one day.")
        return self


class SlotRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_preferences_version: int = Field(strict=True, ge=1)
    date: str = Field(pattern=r"^(today|tomorrow|\d{4}-\d{2}-\d{2})$")
    days: int = Field(default=1, strict=True, ge=1, le=14)
    at_time: str | None = Field(default=None, min_length=1, max_length=20)
    meridiem: Literal["AM", "PM"] | None = None
    fold: int | None = Field(default=None, strict=True, ge=0, le=1)
    time_context: TimeContext | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    duration_minutes: int | None = Field(default=None, strict=True, ge=5, le=480)
    count: int = Field(default=3, strict=True, ge=1, le=3)
    participant_timezones: list[str] = Field(default_factory=list, max_length=5)
    anchor_from_request_id: UUID | None = None

    _zone = field_validator("timezone")(valid_zone)

    @field_validator("participant_timezones")
    @classmethod
    def zones(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Participant display zones must be distinct.")
        for zone in value:
            if len(zone) > 100:
                raise ValueError("Timezone is too long.")
            valid_zone(zone)
        return value

    @model_validator(mode="after")
    def precise_request(self):
        from app.calendar.time_resolution import parse_clock

        if self.at_time is None and any(
            item is not None for item in (self.meridiem, self.fold, self.time_context)
        ):
            raise ValueError("Clock context requires at_time.")
        if self.at_time is not None:
            if self.days != 1:
                raise ValueError("An exact time check is limited to one date.")
            parse_clock(self.at_time, self.meridiem)
        if self.date not in {"today", "tomorrow"}:
            from datetime import date

            date.fromisoformat(self.date)
        return self


class SlotOption(StrictModel):
    id: str
    start: datetime
    end: datetime
    timezone: str
    start_local: str
    end_local: str
    participant_displays: list[dict[str, str]]


class SlotRequestOut(StrictModel):
    id: str
    state: Literal["processing", "needs_clarification", "complete", "unknown", "failed"]
    anchor_at: datetime
    anchor_source: Literal["request_received", "prior_request"]
    anchor_from_request_id: str | None
    preferences_version: int
    account_version: int
    policy_version: str
    created_at: datetime
    expires_at: datetime
    resolution: dict | None
    evidence_id: str | None
    calculated_at: datetime | None
    slots: list[SlotOption]
    reason: str | None
    error_code: str | None
    availability_scope: Literal["user_selected_calendars"] = "user_selected_calendars"
    reservation: Literal[False] = False
