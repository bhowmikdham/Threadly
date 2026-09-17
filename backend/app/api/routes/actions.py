"""Owned exact previews, pilot-gated approvals and explicit stop decisions."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions import approval, calendar_preview, email_preview, gmail_sender
from app.api.deps import CurrentUser
from app.db.engine import get_session
from app.schemas.actions import (
    ActionDecisionRequest,
    ActionDecisionView,
    ApproveActionRequest,
    EmailActionView,
    ProposeEmailAction,
)
from app.schemas.calendar_action import ProposeCalendarAction

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


async def decision_response(session, user_id, action_id, result, response):
    result["action"] = await email_preview.view(session, user_id, action_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/actions/{action_id}/approve", response_model=ActionDecisionView, status_code=202)
async def approve_action(
    action_id: str,
    body: ApproveActionRequest,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    # Client input cannot enable dispatch; server configuration and pilot enrollment do.
    result = await approval.approve(
        session, user_id, action_id, body, execution_enabled=gmail_sender.enabled(user_id=user_id)
    )
    return await decision_response(session, user_id, action_id, result, response)


@router.post("/actions/{action_id}/reject", response_model=ActionDecisionView)
async def reject_action(
    action_id: str,
    body: ActionDecisionRequest,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    result = await approval.stop(session, user_id, action_id, "reject", body)
    return await decision_response(session, user_id, action_id, result, response)


@router.post("/actions/{action_id}/cancel", response_model=ActionDecisionView)
async def cancel_action(
    action_id: str,
    body: ActionDecisionRequest,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    result = await approval.stop(session, user_id, action_id, "cancel", body)
    return await decision_response(session, user_id, action_id, result, response)


@router.post("/artifacts/{artifact_id}/calendar-actions", status_code=201)
async def propose_calendar(
    artifact_id: str,
    body: ProposeCalendarAction,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    action = await calendar_preview.propose(session, user_id, artifact_id, body)
    result = await calendar_preview.view(session, user_id, action.id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/calendar-actions/{action_id}")
async def get_calendar_action(
    action_id: str, user_id: CurrentUser, session: DB, response: Response
):
    result = await calendar_preview.view(session, user_id, action_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/calendar-actions/{action_id}/approve", status_code=202)
async def approve_calendar(
    action_id: str,
    body: ApproveActionRequest,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    await calendar_preview.approve(session, user_id, action_id, body)
    result = await calendar_preview.view(session, user_id, action_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/calendar-actions/{action_id}/{operation}")
async def stop_calendar(
    action_id: str,
    operation: Literal["reject", "cancel"],
    body: ActionDecisionRequest,
    user_id: CurrentUser,
    session: DB,
    response: Response,
):
    await calendar_preview.view(session, user_id, action_id)  # Type/ownership gate before mutation.
    await session.rollback()  # Release read locks before task-first action mutation.
    result = await approval.stop(session, user_id, action_id, operation, body)
    result["action"] = await calendar_preview.view(session, user_id, action_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
