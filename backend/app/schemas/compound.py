"""Explicit user-selected templates; classifier proposals are not accepted as plans."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import AssistantRequest, DraftOptions, StrictModel


class CompoundRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    context_snapshot_id: str = Field(min_length=1, max_length=36)
    template: Literal["summary_then_reply", "summary_then_compose"]
    summary_in_draft: bool
    draft_options: DraftOptions
    draft_instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")

    @model_validator(mode="after")
    def complete_template(self):
        if not self.draft_options.to:
            raise ValueError("Select at least one To recipient before running the plan")
        if (self.template == "summary_then_reply") != bool(self.draft_options.reply_message_id):
            raise ValueError("Reply needs a selected message; compose cannot have a reply target")
        return self

    def as_request(self):
        intent = "reply" if self.template == "summary_then_reply" else "compose"
        return AssistantRequest(
            schema_version="1.0",
            request_id=self.request_id,
            instruction=f"Summarise the captured thread, then draft a {intent}. "
            + (
                "Include the summary in the draft. "
                if self.summary_in_draft
                else "Keep the summary separate from the draft. "
            )
            + self.draft_instruction,
            intent_hint=intent,
            context_snapshot_id=self.context_snapshot_id,
            continuation=None,
            draft_options=self.draft_options,
        )
