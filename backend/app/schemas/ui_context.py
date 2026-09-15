"""The UI asserts order; only the backend can supply mailbox evidence."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.assistant import StrictModel

MessageId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]


class UIMessageMap(StrictModel):
    schema_version: Literal["1.0"]
    surface: Literal["gmail_thread"]
    thread_version: int = Field(ge=0)
    captured_at: str = Field(min_length=20, max_length=40)
    visible_message_ids: list[MessageId] = Field(min_length=1, max_length=50)
    selected_message_ids: list[MessageId] = Field(max_length=50)

    @field_validator("captured_at")
    @classmethod
    def aware_timestamp(cls, value):
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Capture time requires a UTC offset")
        return value

    @model_validator(mode="after")
    def unique_owned_selection(self):
        visible, selected = self.visible_message_ids, self.selected_message_ids
        if len(set(visible)) != len(visible) or len(set(selected)) != len(selected):
            raise ValueError("Reference IDs must be unique within each list")
        if not set(selected).issubset(visible):
            raise ValueError("Selection must belong to the captured visible map")
        return self


class UIContextSnapshotRequest(StrictModel):
    schema_version: Literal["1.1"]
    thread_id: str = Field(min_length=1, max_length=128)
    ui_map: UIMessageMap
