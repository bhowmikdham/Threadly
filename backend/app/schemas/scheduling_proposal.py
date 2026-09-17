"""Read-only scheduling interpretation; confirmation never approves a write."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import StrictModel
from app.schemas.command_plan import CommandClause, WordSpan


class SchedulingProposalRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    expected_preferences_version: int = Field(ge=1)
    context_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    anchor_message_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def anchor(self):
        if self.anchor_message_id and not self.context_snapshot_id:
            raise ValueError("A message anchor requires saved context")
        return self


class ExtractedScheduling(StrictModel):
    clauses: list[CommandClause] = Field(min_length=1, max_length=20)
    operation: Literal["check_time", "suggest_slots"]
    date: WordSpan | None
    time: WordSpan | None
    duration: WordSpan | None
    count: WordSpan | None
    timezone: WordSpan | None
    daypart: WordSpan | None
    unhandled: list[WordSpan] = Field(max_length=20)


class ConfirmSchedulingProposal(StrictModel):
    proposal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_complete_request: bool

    @model_validator(mode="after")
    def explicit_review(self):
        if not self.confirm_complete_request:
            raise ValueError("Confirm the complete request before dispatch")
        return self
