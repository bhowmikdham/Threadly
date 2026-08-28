from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from threadly_common.errors import APIError
from threadly_common.models import ChatRequest
from threadly_common.requestid import HEADER as REQUEST_ID_HEADER
from threadly_common.sse import SSE_HEADERS

from .. import clients, config, security
from ..ratelimit import RateLimiter

router = APIRouter()

chat_limiter = RateLimiter(config.CHAT_RATE_LIMIT_PER_MINUTE)


async def rate_limited_user(user_id: int = Depends(security.current_user)) -> int:
    if not chat_limiter.allow(user_id):
        raise APIError(
            "rate_limited",
            f"Chat is limited to {config.CHAT_RATE_LIMIT_PER_MINUTE} requests per minute.",
            status=429,
        )
    return user_id


@router.post("/chat")
async def chat(body: ChatRequest, request: Request, user_id: int = Depends(rate_limited_user)):
    """The chat entrypoint. The gateway authenticates, rate-limits, and streams
    the orchestrator's SSE byte-for-byte back to the extension."""
    headers = security.internal_headers(user_id)
    headers[REQUEST_ID_HEADER] = request.state.request_id

    async def stream():
        async with clients.http.stream(
            "POST",
            f"{config.ORCHESTRATOR_URL}/v1/orchestrate",
            json={"user_id": user_id, "message": body.message, "thread_id": body.thread_id},
            headers=headers,
        ) as resp:
            async for chunk in resp.aiter_raw():
                yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
