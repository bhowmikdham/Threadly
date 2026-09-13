"""Version 1 assistant contracts, aligned with the implementation playbook.

The preview endpoint is deliberately separate from future durable requests.
Model proposals cannot contain tool URLs, caller identities or executable writes.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal["summarise", "plan_schedule", "reply", "compose", "other"]
Operation = Literal[
    "summarise_thread",
    "plan_actions",
    "check_time",
    "suggest_slots",
    "draft_reply",
    "draft_new",
    "lookup_entity",
    "search_mail",
    "lookup_commitments",
    "transform_text",
    "help",
]
OutputKind = Literal["summary", "draft", "plan", "schedule_options", "answer", "clarification"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Continuation(StrictModel):
    task_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    question_id: str | None


class AssistantRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    intent_hint: Intent | None
    context_snapshot_id: str | None
    continuation: Continuation | None


class RouteParameters(StrictModel):
    date_phrase: str | None
    time_phrase: str | None
    duration_minutes: int | None = Field(ge=5, le=480)
    slot_count: int | None = Field(ge=1, le=3)
    tone: str | None
    recipient_refs: list[str]

    @classmethod
    def empty(cls):
        return cls(
            date_phrase=None,
            time_phrase=None,
            duration_minutes=None,
            slot_count=None,
            tone=None,
            recipient_refs=[],
        )


class RouteDecision(StrictModel):
    schema_version: Literal["1.0"]
    status: Literal["ready", "needs_clarification", "unsupported"]
    intent: Intent
    output_kind: OutputKind
    operations: list[Operation] = Field(max_length=4)
    context_snapshot_id: str | None
    parameters: RouteParameters
    missing_fields: list[str]
    clarification: str | None
    rationale: str = Field(max_length=500)
    requested_action: Literal["none", "send_email", "create_event"]

    @model_validator(mode="after")
    def coherent_status(self):
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("Duplicate operations are not allowed")
        if self.status == "ready" and (self.missing_fields or self.clarification):
            raise ValueError("A ready route cannot need clarification")
        if self.status == "needs_clarification" and (
            not self.missing_fields or not self.clarification or not self.clarification.strip()
        ):
            raise ValueError("Clarification requires missing fields and a question")
        if self.status == "unsupported" and (self.operations or self.requested_action != "none"):
            raise ValueError("Unsupported routes cannot propose operations or actions")
        return self


class RoutePreviewRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    intent_hint: Intent | None = None


class RoutePreview(StrictModel):
    decision: RouteDecision
    router_version: str
    source: Literal["rule", "model"]
    execution_ready: Literal[False] = False
