from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from threadly_common.errors import APIError, install_error_handlers
from threadly_common.models import OrchestrateRequest
from threadly_common.requestid import install_request_id
from threadly_common.sse import SSE_HEADERS

from . import config, db, inference_client, orchestrator, rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()
    await inference_client.http.aclose()


app = FastAPI(title="Threadly Orchestrator", lifespan=lifespan)
install_error_handlers(app)
install_request_id(app)


async def internal_only(x_threadly_internal: str | None = Header(default=None)):
    if x_threadly_internal != config.INTERNAL_TOKEN:
        raise APIError("forbidden", "Internal endpoints require the service token.", status=403)


@app.get("/healthz")
async def healthz():
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "service": "orchestrator"}


@app.post("/v1/orchestrate", dependencies=[Depends(internal_only)])
async def orchestrate(body: OrchestrateRequest, request: Request):
    return StreamingResponse(
        orchestrator.handle(body.user_id, body.message, body.thread_id, request.state.request_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


class IndexRequest(BaseModel):
    user_id: int
    docs: list[dict]  # [{"id": gmail_msg_id, "text": body}]


@app.post("/internal/rag/index", dependencies=[Depends(internal_only)])
async def rag_index(body: IndexRequest):
    count = await rag.index_documents(body.user_id, body.docs)
    return {"indexed": count}
