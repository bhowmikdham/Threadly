import asyncio
import datetime as dt
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel
from threadly_common.errors import APIError, install_error_handlers
from threadly_common.requestid import install_request_id

from . import config, crypto, db, gmail_client, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    task = asyncio.create_task(sync.sync_loop())
    yield
    task.cancel()
    await db.close_pool()
    await gmail_client.http.aclose()
    await sync.http.aclose()


app = FastAPI(title="Threadly Gmail Service", lifespan=lifespan)
install_error_handlers(app)
install_request_id(app)


async def internal_only(x_threadly_internal: str | None = Header(default=None)):
    if x_threadly_internal != config.INTERNAL_TOKEN:
        raise APIError("forbidden", "Internal endpoints require the service token.", status=403)


@app.get("/healthz")
async def healthz():
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "service": "gmail", "mode": "mock" if config.DEV_MODE else "real"}


class OAuthExchange(BaseModel):
    code: str


@app.post("/internal/oauth/exchange", dependencies=[Depends(internal_only)])
async def oauth_exchange(body: OAuthExchange):
    """Exchange the OAuth code, store encrypted tokens, return the identity.
    The gateway turns this into a session JWT."""
    try:
        result = await gmail_client.exchange_code(body.code)
    except gmail_client.GmailError as exc:
        raise APIError("oauth_failed", str(exc), status=502)

    tokens = result["tokens"]
    async with db.pool().acquire() as conn:
        user_id = await conn.fetchval(
            """
            INSERT INTO users (email) VALUES ($1)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id
            """,
            result["email"],
        )
        await conn.execute(
            """
            INSERT INTO oauth_tokens (user_id, provider, enc_refresh_token, enc_access_token, access_expires_at)
            VALUES ($1, 'google', $2, $3, $4)
            ON CONFLICT (user_id, provider) DO UPDATE SET
                enc_refresh_token = COALESCE(EXCLUDED.enc_refresh_token, oauth_tokens.enc_refresh_token),
                enc_access_token = EXCLUDED.enc_access_token,
                access_expires_at = EXCLUDED.access_expires_at,
                updated_at = now()
            """,
            user_id,
            crypto.encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None,
            crypto.encrypt(tokens["access_token"]),
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=tokens.get("expires_in", 3600)),
        )

    # First sync in the background so login returns fast.
    asyncio.create_task(sync.sync_user(user_id))
    return {"user_id": user_id, "email": result["email"]}


class SendRequest(BaseModel):
    draft_id: int
    user_id: int


@app.post("/internal/send", dependencies=[Depends(internal_only)])
async def send(body: SendRequest):
    """Deliver an approved draft. The gateway has already enforced the state
    machine; this service owns the actual Gmail call."""
    async with db.pool().acquire() as conn:
        draft = await conn.fetchrow(
            "SELECT id, to_addrs, subject, body, status FROM drafts WHERE id = $1 AND user_id = $2",
            body.draft_id, body.user_id,
        )
        if not draft:
            raise APIError("not_found", "Draft not found.", status=404)
        if draft["status"] != "sending":
            raise APIError("invalid_state", f"Draft is '{draft['status']}', expected 'sending'.", status=409)
        if not draft["to_addrs"]:
            raise APIError("no_recipient", "Draft has no recipients.", status=400)
        has_tokens = bool(await conn.fetchval(
            "SELECT 1 FROM oauth_tokens WHERE user_id = $1 AND provider = 'google' AND enc_refresh_token IS NOT NULL",
            body.user_id,
        ))

    client = gmail_client.client_for(body.user_id, has_tokens)
    try:
        gmail_msg_id = await client.send(list(draft["to_addrs"]), draft["subject"] or "", draft["body"] or "")
    except gmail_client.GmailError as exc:
        raise APIError("send_failed", str(exc), status=502)
    return {"gmail_msg_id": gmail_msg_id}


class SyncTrigger(BaseModel):
    user_id: int | None = None


@app.post("/internal/sync/trigger", dependencies=[Depends(internal_only)])
async def sync_trigger(body: SyncTrigger):
    if body.user_id is not None:
        return {"results": [await sync.sync_user(body.user_id)]}
    return {"results": await sync.sync_all()}
