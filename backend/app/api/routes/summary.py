"""GET /threads/{id}/summary — SSE (W1, live). Wire contract: docs/api-contract.md.

Events: `token` {"text"} (incremental), `done` {"provider"}, `error` {envelope}.
A cache hit emits one token then done, no model call. Caddy runs with
flush_interval -1 so tokens stream through in prod.
"""
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser
from app.api.errors import ApiError, envelope
from app.db.engine import get_session
from app.model_client.providers import ProviderError
from app.orchestrator.orchestrator import summarise_thread

log = logging.getLogger("threadly.api")
router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{thread_id}/summary")
async def summary_sse(thread_id: str, user_id: CurrentUser, session: DB):
    async def events():
        try:
            async for ev in summarise_thread(session, user_id, thread_id):
                if ev.kind in ("token", "cached"):
                    yield {"event": "token", "data": json.dumps({"text": ev.text})}
                elif ev.kind == "done":
                    yield {"event": "done", "data": json.dumps({"provider": ev.provider})}
        except ApiError as exc:
            yield {
                "event": "error",
                "data": json.dumps(envelope(exc.code, exc.message, exc.detail)),
            }
        except ProviderError:
            yield {
                "event": "error",
                "data": json.dumps(envelope(
                    "upstream_model_unavailable", "Summary generation did not complete."
                )),
            }
        except Exception:
            log.exception("summary stream failed")
            yield {
                "event": "error",
                "data": json.dumps(envelope("internal_error", "Summary failed.")),
            }

    return EventSourceResponse(events())
