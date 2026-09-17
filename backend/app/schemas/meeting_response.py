"""Later-mail choice interpretation binds a specific offer and captured message."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import StrictModel


class MeetingResponseRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    context_snapshot_id: str = Field(min_length=1, max_length=36)
    message_id: str = Field(min_length=1, max_length=128)
    offer_id: str = Field(min_length=1, max_length=36)
    expected_negotiation_version: int = Field(ge=1)
    expected_preferences_version: int = Field(ge=1)


class MeetingChoice(StrictModel):
    offer_id: str = Field(min_length=1, max_length=36)
    slot_id: str = Field(min_length=1, max_length=36)
    message_id: str = Field(min_length=1, max_length=128)
    expected_negotiation_version: int = Field(ge=1)


class ExtractedChoice(StrictModel):
    status: Literal["choice", "ambiguous", "declined"]
    option: int | None = Field(ge=1, le=3)
    quote: str = Field(max_length=1500)

    @model_validator(mode="after")
    def coherent(self):
        if (self.status == "choice") != (self.option is not None):
            raise ValueError("Only an explicit choice has an option number")
        if self.status == "choice" and not self.quote.strip():
            raise ValueError("Choice needs a supporting quote")
        return self
