"""Explicit user preferences and bounded, offset-aware Calendar read contracts."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CALENDARS = 10
MAX_WINDOW_DAYS = 31
POLICY_VERSION = "calendar-read-1.0.0"
CalendarId = Annotated[str, Field(min_length=1, max_length=1024)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkingPeriod(StrictModel):
    weekday: int = Field(strict=True, ge=0, le=6)  # Monday = 0
    start_minute: int = Field(strict=True, ge=0, le=1439)
    end_minute: int = Field(strict=True, ge=1, le=1440)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("Split overnight working hours at midnight.")
        return self


class Preferences(StrictModel):
    timezone: str = Field(min_length=1, max_length=100)
    calendar_ids: list[CalendarId] = Field(min_length=1, max_length=MAX_CALENDARS)
    working_periods: list[WorkingPeriod] = Field(min_length=1, max_length=28)
    buffer_before_minutes: int = Field(strict=True, ge=0, le=240)
    buffer_after_minutes: int = Field(strict=True, ge=0, le=240)
    minimum_notice_minutes: int = Field(strict=True, ge=0, le=10080)
    default_duration_minutes: int = Field(strict=True, ge=5, le=480)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Choose a valid IANA timezone.") from None
        return value

    @field_validator("calendar_ids")
    @classmethod
    def valid_ids(cls, value):
        if len(set(value)) != len(value) or any(
            item != item.strip() or any(ord(c) < 32 or ord(c) == 127 for c in item)
            for item in value
        ):
            raise ValueError("Calendar IDs must be distinct, nonempty and free of controls.")
        return sorted(value)

    @field_validator("working_periods")
    @classmethod
    def nonoverlapping(cls, value):
        ordered = sorted(value, key=lambda item: (item.weekday, item.start_minute))
        for before, after in zip(ordered, ordered[1:], strict=False):
            if before.weekday == after.weekday and before.end_minute > after.start_minute:
                raise ValueError("Working periods cannot overlap.")
        return ordered


class SavePreferences(StrictModel):
    expected_version: int = Field(strict=True, ge=0)
    preferences: Preferences


def parse_instant(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Use an offset-aware timestamp.") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use an offset-aware timestamp.")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError("Timestamp is outside the supported range.") from None


class FreeBusyRequest(StrictModel):
    expected_preferences_version: int = Field(strict=True, ge=1)
    start: datetime
    end: datetime

    _aware = field_validator("start", "end", mode="before")(parse_instant)

    @model_validator(mode="after")
    def bounded(self):
        if not timedelta(0) < self.end - self.start <= timedelta(days=MAX_WINDOW_DAYS):
            raise ValueError("Calendar window must be positive and at most 31 days.")
        return self


class BusyInterval(StrictModel):
    start: datetime
    end: datetime


class CalendarCoverage(StrictModel):
    calendar_id: str
    status: Literal["known", "unknown"]
    reason: Literal["provider_error", "missing", "malformed", "not_accessible"] | None = None
    busy: list[BusyInterval]


class PreferencesOut(StrictModel):
    version: int
    account_version: int
    policy_version: str
    preferences: Preferences


class FreeBusyOut(StrictModel):
    id: str
    preferences_version: int
    account_version: int
    policy_version: str
    checked_at: datetime
    expires_at: datetime
    start: datetime
    end: datetime
    coverage: Literal["complete", "unknown"]
    calendars: list[CalendarCoverage]


class CalendarListItem(StrictModel):
    id: str
    summary: str
    access_role: str
    can_read_busy: bool
    event_write_acl: bool


class CalendarListOut(StrictModel):
    account_version: int
    checked_at: datetime
    calendars: list[CalendarListItem]
