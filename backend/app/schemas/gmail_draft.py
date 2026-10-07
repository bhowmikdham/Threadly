"""A deliberate draft-only save of the complete editor, bound to its source."""

from pydantic import Field, model_validator

from app.schemas.draft_review import EditDraftRequest


class CreateGmailDraft(EditDraftRequest):
    artifact_id: str | None = Field(default=None, min_length=1, max_length=36)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_version: int | None = Field(default=None, ge=1)
    draft_id: str | None = Field(default=None, min_length=1, max_length=36)
    from_address: str = Field(min_length=1, max_length=320)
    account_version: int = Field(ge=1)

    @model_validator(mode="after")
    def bound_source(self):
        if bool(self.artifact_id) == bool(self.draft_id):
            raise ValueError("Choose one saved artifact or conversation draft")
        if bool(self.conversation_id) != (self.expected_version is not None):
            raise ValueError("Include the conversation version")
        if self.draft_id and not self.conversation_id:
            raise ValueError("Conversation drafts require their owning conversation")
        if not self.recipients.to:
            raise ValueError("Enter at least one recipient email address")
        return self
