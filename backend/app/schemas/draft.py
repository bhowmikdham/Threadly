from typing import Literal

from pydantic import BaseModel


class DraftRequest(BaseModel):
    thread_id: str
    instruction: str
    tone: Literal["match_my_voice", "formal", "brief"] = "match_my_voice"


class DraftOut(BaseModel):
    draft_id: int
    body: str
    status: Literal["draft", "approved", "sent", "discarded"]
