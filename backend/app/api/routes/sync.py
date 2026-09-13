"""POST /sync — pull the mailbox now (module 3). W1 runs it inline; a scheduled
loop can reuse the same worker later. Contract: docs/api-contract.md."""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.auth import service as auth_service
from app.db import repositories as repo
from app.db.engine import get_session
from app.sync import worker

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_session)]


class SyncOut(BaseModel):
    mode: str
    messages_upserted: int
    threads_touched: int


@router.post("", response_model=SyncOut)
async def run_sync(user_id: CurrentUser, session: DB) -> SyncOut:
    user = await repo.get_user(session, user_id)
    if user is None:
        raise ApiError(401, "unauthorized", "Unknown user.")
    token = await auth_service.get_valid_access_token(session, user)
    report = await worker.incremental_sync(session, user, token)
    return SyncOut(
        mode=report.mode,
        messages_upserted=report.messages_upserted,
        threads_touched=report.threads_touched,
    )
