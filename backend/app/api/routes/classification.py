"""Read-only classification for a selected thread; clients own transient badges."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentSession, get_current_session
from app.api.errors import ApiError
from app.classification.contracts import ClassificationRequest, ClassificationResponse
from app.classification.service import classify
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import User

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]
log = logging.getLogger("uvicorn.error")


@router.post("/{thread_id}/classification", response_model=ClassificationResponse)
async def classify_thread(
    thread_id: str, body: ClassificationRequest, principal: CurrentSession,
    session: DB, response: Response, authorization: Annotated[str | None, Header()] = None,
) -> ClassificationResponse:
    response.headers["Cache-Control"] = "no-store"
    # Authentication reads open a transaction. Close it before ANY Gmail/model IO.
    mailbox = await session.scalar(select(User.email).where(User.id == principal.user_id))
    await session.rollback()
    if mailbox is None:
        raise ApiError(401, "unauthorized", "Unknown account.")
    started = time.monotonic()
    outcome, code = "error", "internal_error"
    try:
        result = await classify(
            principal.user_id, principal.account_version, mailbox, thread_id, body,
        )
        # Check logout/reconnect/token expiry independently after external work.
        try:
            await get_current_session(session, authorization, get_settings())
        finally:
            await session.rollback()
        outcome, code = result.status, "ok"
        return result
    except ApiError as error:
        code = error.code
        raise
    finally:
        # Operational evidence without account/thread IDs, labels, source text or provider errors.
        log.info("classification_request outcome=%s code=%s duration_ms=%d",
                 outcome, code, round((time.monotonic() - started) * 1000))
