"""Reviewed, case-specific regression constraints; not a general entailment judge."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.assistant import StrictModel

SummaryArray = Literal["decisions", "actions", "open_questions"]


class SummaryChecks(StrictModel):
    empty_fields: list[SummaryArray] = Field(default_factory=list, max_length=3)
    nonempty_fields: list[SummaryArray] = Field(default_factory=list, max_length=3)
    forbidden_overview_phrases: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent(self):
        if set(self.empty_fields) & set(self.nonempty_fields):
            raise ValueError("Contradictory summary checks")
        if any(not phrase.strip() for phrase in self.forbidden_overview_phrases):
            raise ValueError("Empty forbidden phrase")
        return self

    def failures(self, output: dict) -> list[str]:
        failures = [f"{field}:expected_empty" for field in self.empty_fields if output[field]]
        failures += [
            f"{field}:expected_nonempty" for field in self.nonempty_fields if not output[field]
        ]
        overview = " ".join(output["overview"].casefold().split())
        failures += [
            f"overview:forbidden_phrase:{phrase}"
            for phrase in self.forbidden_overview_phrases
            if " ".join(phrase.casefold().split()) in overview
        ]
        return failures
