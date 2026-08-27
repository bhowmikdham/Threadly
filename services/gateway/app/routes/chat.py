from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from threadly_common.models import ChatRequest
from threadly_common.sse import SSE_HEADERS

from .. import clients, config, security

router = APIRouter()


@router.post("/chat")
async def chat(body: ChatRequest, user_id: int = Depends(security.current_user)):
    """The chat entrypoint. The gateway authenticates and streams the
    orchestrator's SSE byte-for-byte back to the extension."""

    async def stream():
        async with clients.http.stream(
            "POST",
            f"{config.ORCHESTRATOR_URL}/v1/orchestrate",
            json={"user_id": user_id, "message": body.message, "thread_id": body.thread_id},
            headers=security.internal_headers(user_id),
        ) as resp:
            async for chunk in resp.aiter_raw():
                yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
