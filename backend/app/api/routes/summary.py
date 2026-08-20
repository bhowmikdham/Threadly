"""Thread summary over SSE (module 5 pipeline; cache key thread_id+last_msg_id).

SSE event shapes are contract: docs/api-contract.md. Caddy is configured with
flush_interval -1 so tokens stream through.
"""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.get("/{thread_id}/summary")
async def summarise_thread(thread_id: str, user_id: CurrentUser):
    # W1: EventSourceResponse streaming `token` events, terminal `done`.
    # Cache hit (summaries table) emits one token + done without touching a model.
    raise not_implemented("Thread summary", "W1")
