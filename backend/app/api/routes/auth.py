"""OAuth state/PKCE handshake, account-bound reconnect, and Threadly JWT renewal."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.auth import flow, service
from app.db.engine import get_session


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(dependencies=[Depends(no_store)])
DB = Annotated[AsyncSession, Depends(get_session)]


class BeginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class BeginOut(BaseModel):
    state: str
    authorization_url: str
    expires_at: str


class DisconnectOut(BaseModel):
    connected: bool
    provider_revocation: str


class ExchangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=1, max_length=4096)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None


class ExchangeOut(BaseModel):
    jwt: str
    user: UserOut


@router.post("/google/begin", response_model=BeginOut)
async def google_begin(body: BeginIn, session: DB):
    result = await flow.begin(session, body.redirect_uri, body.code_challenge)
    await session.commit()
    return result


@router.post("/google/reconnect", response_model=BeginOut)
async def google_reconnect(body: BeginIn, user_id: CurrentUser, session: DB):
    result = await flow.begin(session, body.redirect_uri, body.code_challenge, user_id=user_id)
    await session.commit()
    return result


@router.post("/google/exchange", response_model=ExchangeOut)
async def google_exchange(body: ExchangeIn, session: DB) -> ExchangeOut:
    owner, version = await flow.consume(body.state, body.redirect_uri, body.code_verifier)
    token, user = await service.exchange_code(
        session,
        body.code,
        body.redirect_uri,
        code_verifier=body.code_verifier,
        expected_user_id=owner,
        expected_version=version,
    )
    await session.commit()
    return ExchangeOut(
        jwt=token, user=UserOut(id=user.id, email=user.email, name=user.display_name)
    )


@router.post("/google/disconnect", response_model=DisconnectOut)
async def google_disconnect(user_id: CurrentUser):
    await service.disconnect_google_account(user_id)
    return {"connected": False, "provider_revocation": "not_requested"}


class RefreshOut(BaseModel):
    jwt: str


@router.post("/refresh", response_model=RefreshOut)
async def refresh(user_id: CurrentUser) -> RefreshOut:
    return RefreshOut(jwt=service.issue_session_jwt(user_id))
