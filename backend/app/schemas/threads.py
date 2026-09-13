from datetime import datetime

from pydantic import BaseModel


class ThreadOut(BaseModel):
    thread_id: str
    subject: str | None
    last_msg_at: datetime | None
    needs_reply: bool | None
    version: int = 0
    snippet: str | None = None


class ThreadListOut(BaseModel):
    threads: list[ThreadOut]
    next_page: int | None = None
