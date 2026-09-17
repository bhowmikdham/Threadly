"""One reviewed entry point; classifier labels never grant execution authority."""

from typing import Literal

from pydantic import Field

from app.schemas.assistant import StrictModel
from app.schemas.command_plan import CommandPlanRequest, ProposedCommand
from app.schemas.scheduling_proposal import ExtractedScheduling


class CoordinatorRequest(CommandPlanRequest):
    expected_preferences_version: int | None = Field(default=None, ge=1)
    anchor_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    transform_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class CoordinatedCommand(StrictModel):
    command: ProposedCommand
    scheduling: ExtractedScheduling | None
    other_operation: Literal["help", "transform_text", "lookup_entity", "lookup_commitments"] | None
