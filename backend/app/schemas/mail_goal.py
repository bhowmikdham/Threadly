"""User-grounded interpretations for read/review preparation, never write authority."""

from typing import Literal

from pydantic import Field

from app.schemas.assistant import StrictModel


class MailGoal(StrictModel):
    source: str = Field(min_length=1, max_length=4000)
    continue_previous: bool = False
    entity: str = Field(default="", max_length=200)
    sender_name: str = Field(default="", max_length=200)
    purpose: Literal["discovery", "receipt", "application"] | None = None
    latest: bool | None = None


class ReplyPreparation(StrictModel):
    operation: Literal["reply", "resolve_target"]
    source: str = Field(min_length=1, max_length=4000)
    continue_previous: bool = False
    target: Literal["reference", "latest_inbound"] = "reference"


class MailAssessment(StrictModel):
    reference: str = Field(min_length=1, max_length=40)
    disposition: Literal["relevant", "irrelevant", "uncertain"]
    quote: str = Field(min_length=1, max_length=500)
