"""References to user-authored dialogue; never action authority or provider evidence."""

from pydantic import Field

from app.schemas.assistant import StrictModel


class UserCitation(StrictModel):
    turn_version: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


class FieldCitation(UserCitation):
    field: str = Field(min_length=1, max_length=40)


def validate_fields(citations, allowed):
    fields = [item.field for item in citations]
    if len(fields) != len(set(fields)) or not set(fields).issubset(allowed):
        raise ValueError("Cite each supported field at most once")
    if sum(len(item.quote) for item in citations) > 8000:
        raise ValueError("Use at most 8000 characters of cited user context")
