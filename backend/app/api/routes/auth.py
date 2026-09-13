"""Auth routes (module 2, W1 — live). Shapes: docs/api-contract.md."""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.auth import service
from app.db.engine import get_session

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_session)]


class ExchangeIn(BaseModel):
    code: str
    redirect_uri: str  # the chrome.identity redirect used by the extension


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None


class ExchangeOut(BaseModel):
    jwt: str
    user: UserOut


@router.post("/google/exchange", response_model=ExchangeOut)
async def google_exchange(body: ExchangeIn, session: DB) -> ExchangeOut:
    token, user = await service.exchange_code(session, body.code, body.redirect_uri)
    return ExchangeOut(
        jwt=token, user=UserOut(id=user.id, email=user.email, name=user.display_name)
    )


class RefreshOut(BaseModel):
    jwt: str


@router.post("/refresh", response_model=RefreshOut)
async def refresh(user_id: CurrentUser) -> RefreshOut:
    # valid JWT in => fresh JWT out (sliding session)
    return RefreshOut(jwt=service.issue_session_jwt(user_id))
