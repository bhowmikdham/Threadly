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
