"""POST /sync — pull the mailbox now (module 3). W1 runs it inline; a scheduled
loop can reuse the same worker later. Contract: docs/api-contract.md."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.auth import service as auth_service
from app.db import repositories as repo
from app.db.engine import get_session
from app.sync import jobs, worker
from app.sync.gmail import GmailError

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
    try:
        report = await worker.incremental_sync(session, user, token)
    except worker.SyncConflict as exc:
        raise ApiError(
            409, "sync_conflict", "Another sync completed. Retry from current state."
        ) from exc
    except GmailError as exc:
        if exc.status == 401:
            raise ApiError(401, "gmail_reauth_required", "Reconnect your Google account.") from exc
        raise ApiError(
            503, "gmail_sync_unavailable", "Gmail sync did not complete. Retry later."
        ) from exc
    return SyncOut(
        mode=report.mode,
        messages_upserted=report.messages_upserted,
        threads_touched=report.threads_touched,
    )


class SyncJobRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")


@router.post("/jobs", status_code=202)
async def queue_sync(body: SyncJobRequest, user_id: CurrentUser, session: DB):
    row = await jobs.submit(session, user_id, body.request_id)
    result = jobs.view(row)
    await session.commit()
    return result


@router.get("/jobs/{job_id}")
async def get_sync_job(job_id: str, user_id: CurrentUser, session: DB):
    return jobs.view(await jobs.owned(session, user_id, job_id))
