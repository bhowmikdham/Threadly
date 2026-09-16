"""Authenticated Google capability readiness endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.capabilities.service import build_capabilities
from app.db import repositories as repo
from app.db.engine import get_session
from app.schemas.capabilities import GoogleCapabilities

router = APIRouter()


@router.get("/capabilities", response_model=GoogleCapabilities)
async def get_capabilities(
    user_id: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    user = await repo.get_user(session, user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "Authentication is required.")
    return build_capabilities(user)
