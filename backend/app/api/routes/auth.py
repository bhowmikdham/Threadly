"""OAuth state/PKCE handshake, account-bound reconnect, and Threadly JWT renewal."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentSession, RenewalSession
from app.api.errors import ApiError
from app.auth import flow, service
from app.auth.limits import OAuthIngressRoute
from app.db.engine import get_session


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(dependencies=[Depends(no_store)], route_class=OAuthIngressRoute)
DB = Annotated[AsyncSession, Depends(get_session)]


class BeginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class ReconnectIn(BeginIn):
    capabilities: list[
        Literal[
            "gmail_read",
            "calendar_read",
            "calendar_events_read",
            "gmail_send",
            "calendar_write",
        ]
    ] = Field(default_factory=list, max_length=5)


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
    refresh_token: str
    user: UserOut


@router.post("/google/begin", response_model=BeginOut)
async def google_begin(body: BeginIn, session: DB):
    result = await flow.begin(session, body.redirect_uri, body.code_challenge)
    await session.commit()
    return result


@router.post("/google/reconnect", response_model=BeginOut)
async def google_reconnect(body: ReconnectIn, authenticated: CurrentSession, session: DB):
    result = await flow.begin(
        session,
        body.redirect_uri,
        body.code_challenge,
        user_id=authenticated.user_id,
        expected_account_version=authenticated.account_version,
        expected_session_version=authenticated.session_version,
        calendar_read="calendar_read" in body.capabilities,
        calendar_events_read="calendar_events_read" in body.capabilities,
        gmail_send="gmail_send" in body.capabilities,
        calendar_write="calendar_write" in body.capabilities,
    )
    await session.commit()
    return result


@router.post("/google/exchange", response_model=ExchangeOut)
async def google_exchange(body: ExchangeIn, session: DB) -> ExchangeOut:
    owner, version, session_version = await flow.consume(
        body.state, body.redirect_uri, body.code_verifier
    )
    token, user = await service.exchange_code(
        session,
        body.code,
        body.redirect_uri,
        code_verifier=body.code_verifier,
        expected_user_id=owner,
        expected_version=version,
        expected_session_version=session_version,
    )
    await session.commit()
    return ExchangeOut(
        jwt=token,
        user=UserOut(id=user.id, email=user.email, name=user.display_name),
        refresh_token=service.refresh_token_for(token),
    )


@router.post("/google/disconnect", response_model=DisconnectOut)
async def google_disconnect(authenticated: CurrentSession):
    disconnected = await service.disconnect_google_account(
        authenticated.user_id,
        expected_version=authenticated.account_version,
        expected_session_version=authenticated.session_version,
    )
    if not disconnected:
        raise ApiError(409, "google_connection_changed", "Google connection changed; retry.")
    return {"connected": False, "provider_revocation": "not_requested"}


class RefreshOut(BaseModel):
    jwt: str
    refresh_token: str


class LogoutOut(BaseModel):
    signed_out: bool
    scope: Literal["all_sessions"]


@router.post("/logout", response_model=LogoutOut)
async def logout(authenticated: RenewalSession, session: DB) -> LogoutOut:
    await service.logout_threadly_session(
        session,
        user_id=authenticated.user_id,
        expected_account_version=authenticated.account_version,
        expected_session_version=authenticated.session_version,
    )
    return LogoutOut(signed_out=True, scope="all_sessions")


@router.post("/refresh", response_model=RefreshOut)
async def refresh(authenticated: RenewalSession, session: DB) -> RefreshOut:
    token = await service.refresh_session_jwt(
        session,
        user_id=authenticated.user_id,
        expected_account_version=authenticated.account_version,
        expected_session_version=authenticated.session_version,
        refresh_expires_at=authenticated.refresh_expires_at,
    )
    return RefreshOut(jwt=token, refresh_token=service.refresh_token_for(token))
