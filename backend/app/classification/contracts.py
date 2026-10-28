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


class SecurityPriority(StrictModel):
    """Counterfactual priority only; never an authenticity or safety verdict."""

    source_id: str = Field(min_length=1, max_length=32)
    priority_if_recognised: Priority
    evidence: Sources


class Decision(StrictModel):
    status: Literal["classified", "needs_review"]
    labels: Labels | None
    evidence: Evidence | None
    reason_codes: list[Reason] = Field(min_length=1, max_length=7)
    security_priority: SecurityPriority | None = None

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
        if self.security_priority:
            rank = {"Low": 0, "Medium": 1, "High": 2}
            if (self.labels is None or self.status != "classified"
                    or rank[self.security_priority.priority_if_recognised]
                    > rank[self.labels.priority]):
                raise ValueError("Invalid recognition baseline")
            if len(set(self.security_priority.evidence)) != len(self.security_priority.evidence):
                raise ValueError("Duplicate recognition evidence")
        return self


class IncomingMessage(StrictModel):
    """Provider metadata only; never a security/authenticity verdict."""

    message_id: str = Field(pattern=r"^[a-f0-9]{1,32}$")
    received_at: datetime
    timestamp_source: Literal["gmail.internalDate"] = "gmail.internalDate"


class SecurityPriorityResponse(StrictModel):
    message_id: str = Field(pattern=r"^[a-f0-9]{1,32}$")
    priority_if_recognised: Priority
    evidence: Sources


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
    latest_incoming: IncomingMessage | None = None
    security_priority: SecurityPriorityResponse | None = None

    @model_validator(mode="after")
    def consistent_state(self):
        if self.status == "classified":
            if self.labels is None or self.evidence is None:
                raise ValueError("Classified requires labels and evidence")
        elif self.labels is not None or self.evidence is not None:
            raise ValueError("Unclassified states cannot carry badges")
        if self.security_priority:
            baseline = self.security_priority
            rank = {"Low": 0, "Medium": 1, "High": 2}
            if (self.status != "classified" or self.latest_incoming is None
                    or baseline.message_id != self.latest_incoming.message_id
                    or baseline.message_id not in self.source_message_ids
                    or not set(baseline.evidence) <= set(self.source_message_ids)
                    or len(set(baseline.evidence)) != len(baseline.evidence)
                    or rank[baseline.priority_if_recognised] > rank[self.labels.priority]):
                raise ValueError("Unbound recognition baseline")
        return self
