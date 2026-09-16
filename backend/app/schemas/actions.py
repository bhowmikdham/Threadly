"""Internal backend-built candidate, not a public arbitrary-payload approval API."""

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue

from app.schemas.assistant import StrictModel


class ActionCandidate(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    action_type: Literal["send_email", "create_event"]
    payload_schema: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    payload: dict[str, JsonValue] = Field(min_length=1)
    source_versions: dict[str, JsonValue]
    expires_at: AwareDatetime


class ProposeEmailAction(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    action_type: Literal["send_email"]


class EmailPreview(StrictModel):
    from_address: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    message_id: str
    date: str
    in_reply_to: str | None
    references: list[str]
    gmail_thread_id: str | None


class EmailRecoveryView(StrictModel):
    status: Literal["manual_inspection", "paused", "checking", "scheduled"]
    rounds: int = Field(ge=0, le=3)
    max_rounds: Literal[3] = 3
    next_check_at: str | None
    last_code: str | None
    guidance: str


class EmailActionView(StrictModel):
    action_id: str
    task_id: str
    artifact_id: str
    action_type: Literal["send_email"]
    state: str
    version: int
    payload_schema: str
    payload_hash: str
    mime_sha256: str
    expires_at: str
    preview: EmailPreview
    account_version: int
    blockers: list[str]
    approval_available: Literal[False] = False
    sending_available: Literal[False] = False
    authorization: Literal["none", "exact_payload_approval"] = "none"
    approval_id: str | None = None
    cancellation_requested: bool = False
    allowed_operations: list[Literal["reject", "cancel"]] = Field(default_factory=list)
    result: dict[str, str] | None = None
    error_code: str | None = None
    recovery: EmailRecoveryView | None = None


class ActionDecisionRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_version: int = Field(ge=1)


class ApproveActionRequest(ActionDecisionRequest):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ActionDecisionView(StrictModel):
    request_id: str
    operation: Literal["approve", "reject", "cancel"]
    decision: Literal["approved", "rejected", "cancelled", "cancellation_requested"]
    action: EmailActionView
