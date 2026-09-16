"""Command interpretation is a reviewable proposal, never execution authority."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import DraftOptions, StrictModel

CommandOperation = Literal[
    "summary",
    "reply",
    "compose",
    "search_capture",
    "search_mailbox",
    "schedule",
    "plan",
    "other",
    "send",
    "book",
]


class WordSpan(StrictModel):
    start: int = Field(ge=1, le=4000)
    end: int = Field(ge=1, le=4000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Word span must be ordered")
        return self


class CommandClause(WordSpan):
    kind: Literal["requested", "prohibited", "context"]
    operations: list[CommandOperation] = Field(max_length=10)

    @model_validator(mode="after")
    def coherent(self):
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("Duplicate clause operation")
        if (self.kind == "context") != (not self.operations):
            raise ValueError("Only context clauses have no operation")
        return self


class ProposedCommand(StrictModel):
    clauses: list[CommandClause] = Field(min_length=1, max_length=20)
    summary_usage: Literal["include", "separate", "not_applicable", "unclear"]
    lookup_query: WordSpan | None
    ambiguities: list[str] = Field(max_length=5)


class CommandPlanRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    context_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    draft_options: DraftOptions | None = None


class ConfirmCommandPlan(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_complete_command: bool

    @model_validator(mode="after")
    def explicit_review(self):
        if not self.confirm_complete_command:
            raise ValueError("Confirm the complete command plan before dispatch")
        return self
