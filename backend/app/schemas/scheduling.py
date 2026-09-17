"""Explicit scheduling contracts. Prose/model classification cannot execute this API."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import AssistantRequest, StrictModel
from app.schemas.slots import SlotRequest, TimeContext


class SchedulingConstraints(StrictModel):
    date: str | None = Field(default=None, min_length=1, max_length=10)
    days: int = Field(default=1, ge=1, le=14)
    at_time: str | None = Field(default=None, min_length=1, max_length=20)
    meridiem: Literal["AM", "PM"] | None = None
    fold: int | None = Field(default=None, ge=0, le=1)
    time_context: TimeContext | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    count: int = Field(default=3, ge=1, le=3)
    participant_timezones: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def valid_slot_fields(self):
        SlotRequest(
            request_id="validation",
            expected_preferences_version=1,
            **{**self.model_dump(), "date": self.date or "today"},
        )
        return self


class SchedulingRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    operation: Literal["check_time", "suggest_slots"]
    expected_preferences_version: int = Field(ge=1)
    context_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    anchor_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    constraints: SchedulingConstraints

    @model_validator(mode="after")
    def coherent(self):
        if self.anchor_message_id and not self.context_snapshot_id:
            raise ValueError("A message anchor requires a saved context.")
        validate_operation(self.operation, self.constraints)
        return self

    def as_request(self):
        return AssistantRequest(
            schema_version="1.0",
            request_id=self.request_id,
            instruction="Check a meeting time"
            if self.operation == "check_time"
            else "Find meeting options",
            intent_hint="plan_schedule",
            context_snapshot_id=self.context_snapshot_id,
            continuation=None,
        )


def validate_operation(operation, constraints):
    if operation == "check_time" and constraints.days != 1:
        raise ValueError("An exact check covers one date.")
    if operation == "suggest_slots" and constraints.at_time is not None:
        raise ValueError("Use check_time for an exact clock time.")


class SchedulingAnswer(StrictModel):
    date: str | None = Field(default=None, min_length=1, max_length=10)
    at_time: str | None = Field(default=None, min_length=1, max_length=20)
    meridiem: Literal["AM", "PM"] | None = None
    fold: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Supply a requested scheduling answer.")
        return self


class SchedulingInputRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_version: int = Field(ge=1)
    question_id: str = Field(min_length=1, max_length=36)
    answer: SchedulingAnswer
