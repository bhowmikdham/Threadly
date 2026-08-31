import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from threadly_common.errors import install_error_handlers
from threadly_common.prodcheck import assert_prod_config
from threadly_common.requestid import install_request_id

from . import clients, config, db
from .routes import auth, chat, commitments, drafts, entities, messages, sync, system, threads, voice

log = logging.getLogger(__name__)


async def _reap_stuck_drafts() -> None:
    """A crash between 'sending' and the terminal state would strand a draft
    forever (sending only transitions to sent/failed). Sweep strays to failed
    so the user can retry."""
    while True:
        try:
            async with db.pool().acquire() as conn:
                done = await conn.execute(
                    """
                    UPDATE drafts SET status = 'failed', error = 'send timed out', updated_at = now()
                    WHERE status = 'sending' AND updated_at < now() - interval '10 minutes'
                    """
                )
            if not done.endswith(" 0"):
                log.warning("draft reaper: recovered stuck drafts (%s)", done)
        except Exception:
            log.exception("draft reaper sweep failed")
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_prod_config(
        "gateway",
        config.DEV_MODE,
        {
            "THREADLY_JWT_SECRET": config.JWT_SECRET,
            "THREADLY_INTERNAL_TOKEN": config.INTERNAL_TOKEN,
            "THREADLY_DATABASE_URL": config.DATABASE_URL,
        },
        min_lengths={"THREADLY_JWT_SECRET": 32, "THREADLY_INTERNAL_TOKEN": 16},
    )
    await db.init_pool()
    reaper = asyncio.create_task(_reap_stuck_drafts())
    yield
    reaper.cancel()
    await db.close_pool()
    await clients.http.aclose()


app = FastAPI(title="Threadly Gateway", lifespan=lifespan)
install_error_handlers(app)
install_request_id(app)

app.include_router(system.router)
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(chat.router, prefix="/v1", tags=["chat"])
app.include_router(threads.router, prefix="/v1", tags=["threads"])
app.include_router(messages.router, prefix="/v1", tags=["messages"])
app.include_router(drafts.router, prefix="/v1", tags=["drafts"])
app.include_router(entities.router, prefix="/v1", tags=["entities"])
app.include_router(sync.router, prefix="/v1", tags=["sync"])
app.include_router(commitments.router, prefix="/v1", tags=["commitments"])
app.include_router(voice.router, prefix="/v1", tags=["voice"])
