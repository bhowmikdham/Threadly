from contextlib import asynccontextmanager

from fastapi import FastAPI
from threadly_common.errors import install_error_handlers

from . import clients, db
from .routes import auth, chat, commitments, drafts, entities, sync, system, threads, voice


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()
    await clients.http.aclose()


app = FastAPI(title="Threadly Gateway", lifespan=lifespan)
install_error_handlers(app)

app.include_router(system.router)
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(chat.router, prefix="/v1", tags=["chat"])
app.include_router(threads.router, prefix="/v1", tags=["threads"])
app.include_router(drafts.router, prefix="/v1", tags=["drafts"])
app.include_router(entities.router, prefix="/v1", tags=["entities"])
app.include_router(sync.router, prefix="/v1", tags=["sync"])
app.include_router(commitments.router, prefix="/v1", tags=["commitments"])
app.include_router(voice.router, prefix="/v1", tags=["voice"])
