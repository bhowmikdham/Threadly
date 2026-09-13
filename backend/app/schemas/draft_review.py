"""Explicit user edits and review acknowledgements; no executable action input."""

from pydantic import Field, field_validator

from app.schemas.assistant import DraftOptions, StrictModel


class DraftRecipients(DraftOptions):
    # Reuse recipient validation but forbid switching reply identity through an edit.
    reply_message_id: None = None


class EditDraftRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=20000)
    recipients: DraftRecipients
    unresolved_fields: list[str] = Field(max_length=25)

    @field_validator("subject", "body", "unresolved_fields")
    @classmethod
    def plain_text(cls, value, info):
        values = value if isinstance(value, list) else [value]
        for text in values:
            if not text.strip() or any(
                (ord(c) < 32 and (info.field_name == "subject" or c not in "\r\n\t"))
                or ord(c) == 127
                for c in text
            ):
                raise ValueError("Use nonblank plain text without unsupported controls")
            if info.field_name == "unresolved_fields" and len(text) > 300:
                raise ValueError("Missing-fact descriptions are limited to 300 characters")
        return value


class ReviewDraftRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
