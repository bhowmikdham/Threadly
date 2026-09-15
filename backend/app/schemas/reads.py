"""Explicit, bounded read actions. Source scope always comes from an owned snapshot."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReadOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["help", "search_mail", "transform_text"]
    query: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    message_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}:[1-9][0-9]?$", max_length=67)

    @model_validator(mode="after")
    def operation_fields(self):
        if self.operation == "search_mail":
            if self.query is None or self.message_id is not None:
                raise ValueError("Search needs a literal query and no message target")
        elif self.operation == "transform_text":
            if self.message_id is None or self.query is not None or self.cursor is not None:
                raise ValueError("Transform needs exactly one selected message")
        elif self.query is not None or self.message_id is not None or self.cursor is not None:
            raise ValueError("Help takes no search or message parameters")
        return self
