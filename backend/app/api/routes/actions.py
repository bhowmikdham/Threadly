"""Exact owned email previews. No approve/send endpoint or action-job enqueue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions import email_preview
from app.api.deps import CurrentUser
from app.db.engine import get_session
from app.schemas.actions import EmailActionView, ProposeEmailAction

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/artifacts/{artifact_id}/actions", response_model=EmailActionView, status_code=201)
async def propose_email(
    artifact_id: str,
    body: ProposeEmailAction,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    action = await email_preview.propose(session, user_id, artifact_id, body)
    result = await email_preview.view(session, user_id, action.id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/actions/{action_id}", response_model=EmailActionView)
async def get_email_action(action_id: str, user_id: CurrentUser, session: DB, response: Response):
    result = await email_preview.view(session, user_id, action_id)
    response.headers["Cache-Control"] = "no-store"
    return result
