"""App factory. Wires routers, error envelope handlers, and CORS.

Module 1 (API layer) starts here; see docs/architecture.md for the module map.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import assistant, auth, draft, entities, health, summary, sync, threads, voice
from app.config import get_settings

log = logging.getLogger("threadly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.is_dev:
        # Dev convenience only: create tables if the DB is reachable.
        # Real schema management is alembic (make db-upgrade).
        try:
            from app.db.engine import create_all_dev

            await create_all_dev()
        except Exception as exc:  # DB not up yet — /healthz must still serve
            log.warning("dev create_all skipped: %s", exc)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Threadly API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_dev else None,
    )

    # The extension calls us from its own origin; browsers still enforce CORS
    # for extension pages. Locked to extension ids once published.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://.*" if settings.is_dev else r"$^",
        allow_origins=["http://localhost:8000"] if settings.is_dev else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(health.router, tags=["health"])
    app.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(sync.router, prefix="/sync", tags=["sync"])
    app.include_router(threads.router, prefix="/threads", tags=["threads"])
    app.include_router(summary.router, prefix="/threads", tags=["summary"])
    app.include_router(draft.router, prefix="/draft", tags=["draft"])
    app.include_router(entities.router, tags=["entities"])
    app.include_router(voice.router, prefix="/voice", tags=["voice"])
    return app


app = create_app()
