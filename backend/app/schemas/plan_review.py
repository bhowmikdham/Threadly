"""Explicit edits and commitment selection; neither authorizes an external write."""

from datetime import date

from pydantic import Field, field_validator, model_validator

from app.schemas.assistant import StrictModel


class PlanItemEdit(StrictModel):
    id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=500, pattern=r"\S")
    owner: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("due_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    depends_on: list[str] = Field(default_factory=list, max_length=12)


class ReviewPlan(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    items: list[PlanItemEdit] | None = Field(default=None, max_length=12)
    accepted_item_ids: list[str] | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def one_change(self):
        if (self.items is None) == (self.accepted_item_ids is None):
            raise ValueError("Either edit the plan or accept selected items")
        return self
