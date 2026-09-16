"""Explicit captured-text lookup followed by one draft; never a free-text plan."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import AssistantRequest, DraftOptions, StrictModel


class LookupDraftRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    context_snapshot_id: str = Field(min_length=1, max_length=36)
    template: Literal["lookup_then_reply", "lookup_then_compose"]
    query: str = Field(min_length=1, max_length=200, pattern=r"\S")
    draft_options: DraftOptions
    draft_instruction: str = Field(min_length=1, max_length=4000, pattern=r"\S")

    @model_validator(mode="after")
    def complete_template(self):
        if not self.draft_options.to:
            raise ValueError("Select at least one To recipient before running the plan")
        if (self.template == "lookup_then_reply") != bool(self.draft_options.reply_message_id):
            raise ValueError("Reply needs a selected message; compose cannot have a reply target")
        return self

    def as_request(self):
        intent = "reply" if self.template == "lookup_then_reply" else "compose"
        return AssistantRequest(
            schema_version="1.0",
            request_id=self.request_id,
            instruction=f"Find literal text in the captured thread, then draft a {intent}. "
            + self.draft_instruction,
            intent_hint=intent,
            context_snapshot_id=self.context_snapshot_id,
            continuation=None,
            draft_options=self.draft_options,
        )
