from fastapi import APIRouter
from pydantic import BaseModel, EmailStr
from threadly_common.errors import APIError

from .. import clients, config, db, security

router = APIRouter()


class DevLogin(BaseModel):
    email: EmailStr


class GoogleExchange(BaseModel):
    code: str


class Refresh(BaseModel):
    refresh_token: str


@router.post("/dev")
async def dev_login(body: DevLogin):
    """Dev-mode login: create/fetch a user by email, no Google involved."""
    if not config.DEV_MODE:
        raise APIError("not_found", "Not available.", status=404)
    async with db.pool().acquire() as conn:
        user_id = await conn.fetchval(
            """
            INSERT INTO users (email) VALUES ($1)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id
            """,
            body.email,
        )
    return {"user_id": user_id, **security.create_session(user_id)}


@router.post("/google/exchange")
async def google_exchange(body: GoogleExchange):
    """OAuth code exchange. The gmail service owns Google credentials and token
    storage; the gateway only turns the resulting identity into a session JWT."""
    resp = await clients.http.post(
        f"{config.GMAIL_SERVICE_URL}/internal/oauth/exchange",
        json={"code": body.code},
        headers=security.internal_headers(),
    )
    if resp.status_code != 200:
        raise APIError("oauth_failed", "Google OAuth exchange failed.", status=502, details=resp.json())
    user = resp.json()
    return {"user_id": user["user_id"], "email": user["email"], **security.create_session(user["user_id"])}


@router.post("/refresh")
async def refresh(body: Refresh):
    user_id = security.decode_token(body.refresh_token, "refresh")
    return {"user_id": user_id, **security.create_session(user_id)}
