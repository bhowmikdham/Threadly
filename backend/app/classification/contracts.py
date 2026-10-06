"""Badge contracts preserve native BERT labels; binary reply is independent."""

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal[
    "finance_payments", "hr", "it", "legal_contracts", "meeting_scheduling", "other", "projects"
]
Priority = Literal["High", "Low", "Medium"]
Action = Literal["approve", "attend", "complete_submit", "edit", "no_action", "reply", "review"]
Reason = Literal[
    "unanswered_request", "request_resolved", "informational", "time_sensitive",
    "routine_work", "ambiguous_purpose", "insufficient_context",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClassificationRequest(StrictModel):
    time_zone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use an IANA time zone") from None
        return value


class Labels(StrictModel):
    needs_reply: bool
    priority: Priority
    category: Category
    action: Action


Sources = Annotated[list[Annotated[str, Field(min_length=1, max_length=32)]],
                    Field(min_length=1, max_length=50)]


class Evidence(StrictModel):
    needs_reply: Sources
    priority: Sources
    category: Sources
    action: Sources

    @field_validator("needs_reply", "priority", "category", "action")
    @classmethod
    def unique_sources(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate source")
        return values


class Decision(StrictModel):
    status: Literal["classified", "needs_review"]
    labels: Labels | None
    evidence: Evidence | None
    reason_codes: list[Reason] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "classified":
            if self.labels is None or self.evidence is None:
                raise ValueError("Classified requires labels and evidence")
            if set(self.reason_codes) & {"insufficient_context", "ambiguous_purpose"}:
                raise ValueError("Uncertainty requires needs_review")
        elif self.labels is not None or self.evidence is not None:
            raise ValueError("Needs review cannot carry labels")
        elif not set(self.reason_codes) <= {"insufficient_context", "ambiguous_purpose"}:
            raise ValueError("Needs review requires an uncertainty reason")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("Duplicate reason")
        return self


class ClassificationResponse(StrictModel):
    schema_version: Literal["email-classification-with-action.v1"] = (
        "email-classification-with-action.v1"
    )
    thread_id: str
    source_message_ids: list[str]
    source_fingerprint: str
    status: Literal["classified", "needs_review", "skipped"]
    labels: Labels | None
    evidence: Evidence | None
    reason_codes: list[str]
    evaluated_at: datetime
    valid_until: datetime
    time_zone: str
    release_id: str
    source: Literal["live_gmail"] = "live_gmail"
    coverage: Literal["cleaned_text_only"] = "cleaned_text_only"
    persisted: Literal[False] = False

    @model_validator(mode="after")
    def consistent_state(self):
        if self.status == "classified":
            if self.labels is None or self.evidence is None:
                raise ValueError("Classified requires labels and evidence")
        elif self.labels is not None or self.evidence is not None:
            raise ValueError("Unclassified states cannot carry badges")
        return self
