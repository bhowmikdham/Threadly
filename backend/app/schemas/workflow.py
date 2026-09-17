"""Reviewed finite MVP workflows. No generated tool IDs or external write steps."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import AssistantRequest, DraftOptions, StrictModel
from app.schemas.meeting_response import MeetingChoice
from app.schemas.scheduling import SchedulingConstraints


class WorkflowSchedule(StrictModel):
    operation: Literal["check_time", "suggest_slots"]
    expected_preferences_version: int = Field(ge=1)
    anchor_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    constraints: SchedulingConstraints


class WorkflowRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    context_snapshot_id: str = Field(min_length=1, max_length=36)
    operations: list[
        Literal[
            "summary",
            "schedule",
            "draft_reply",
            "draft_new",
            "plan",
            "lookup_entity",
            "lookup_commitments",
        ]
    ] = Field(min_length=1, max_length=3)
    schedule: WorkflowSchedule | None = None
    draft_options: DraftOptions | None = None
    summary_in_draft: bool = False
    entity_type: str | None = Field(default=None, max_length=40)
    meeting_choice: MeetingChoice | None = None
    accepted_plan_artifact_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def installed_graph(self):
        # Complete supported graphs only, before any work is queued.
        if self.entity_type is not None and self.operations != ["lookup_entity"]:
            raise ValueError("Entity type belongs only to entity lookup")
        if self.meeting_choice and (
            self.operations != ["schedule"]
            or not self.schedule
            or self.schedule.operation != "check_time"
        ):
            raise ValueError("Meeting choice confirmation only rechecks one exact time")
        installed = [
            ("plan",),
            ("summary",),
            ("schedule",),
            ("draft_reply",),
            ("draft_new",),
            ("lookup_entity",),
            ("lookup_commitments",),
        ]
        for draft in ("draft_reply", "draft_new"):
            installed += [("schedule", draft), ("summary", "schedule", draft)]
        if self.accepted_plan_artifact_id:
            installed += [("draft_reply",), ("draft_new",)]
            if len(self.operations) != 1 or not self.operations[0].startswith("draft_"):
                raise ValueError("A selected plan can only feed a draft")
        if tuple(self.operations) not in installed:
            raise ValueError("This complete workflow is not installed")
        if ("schedule" in self.operations) != (self.schedule is not None):
            raise ValueError("Scheduling needs explicit reviewed constraints")
        draft = self.operations[-1]
        if draft.startswith("draft_"):
            if not self.draft_options or not self.draft_options.to:
                raise ValueError("Select draft recipients")
            if (draft == "draft_reply") != bool(self.draft_options.reply_message_id):
                raise ValueError("Select a matching reply/compose target")
        elif self.draft_options is not None:
            raise ValueError("Plan generation has no outgoing envelope")
        if self.summary_in_draft and "summary" not in self.operations:
            raise ValueError("Including a summary requires a summary step")
        return self

    def as_request(self):
        return AssistantRequest(
            schema_version="1.0",
            request_id=self.request_id,
            instruction=self.instruction,
            intent_hint=(
                {
                    "summary": "summarise",
                    "plan": "plan_schedule",
                    "schedule": "plan_schedule",
                    "draft_reply": "reply",
                    "draft_new": "compose",
                    "lookup_entity": "other",
                    "lookup_commitments": "other",
                }[self.operations[0]]
                if len(self.operations) == 1
                else "plan_schedule"
            ),
            context_snapshot_id=self.context_snapshot_id,
            continuation=None,
            draft_options=self.draft_options,
        )
