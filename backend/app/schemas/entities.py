from datetime import datetime

from pydantic import BaseModel


class EntityOut(BaseModel):
    type: str
    key: str
    value: str
    source_msg_id: str | None


class CommitmentOut(BaseModel):
    id: int
    direction: str
    body: str
    due_at: datetime | None
    status: str
    thread_id: str | None
