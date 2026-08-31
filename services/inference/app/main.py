from fastapi import Depends, FastAPI, Header
from fastapi.responses import StreamingResponse
from threadly_common.errors import APIError, install_error_handlers
from threadly_common.models import ClassifyRequest, EmbedRequest, GenerateRequest
from threadly_common.requestid import install_request_id
from threadly_common.sse import EVENT_DONE, EVENT_TOKEN, SSE_HEADERS, sse_event

from . import classify as classify_mod
from . import config
from .backends import router

from threadly_common.prodcheck import assert_prod_config

assert_prod_config(
    "inference", config.DEV_MODE,
    {"THREADLY_INTERNAL_TOKEN": config.INTERNAL_TOKEN},
    min_lengths={"THREADLY_INTERNAL_TOKEN": 16},
)

app = FastAPI(title="Threadly Inference")
install_error_handlers(app)
install_request_id(app)


async def internal_only(x_threadly_internal: str | None = Header(default=None)):
    if x_threadly_internal != config.INTERNAL_TOKEN:
        raise APIError("forbidden", "Internal endpoints require the service token.", status=403)


@app.get("/healthz")
async def healthz():
    ollama_up = await router.ollama.healthy()
    return {
        "status": "ok",
        "service": "inference",
        "backends": {
            "ollama": "up" if ollama_up else ("unconfigured" if not config.OLLAMA_BASE_URL else "down"),
            "openrouter": "configured" if router.openrouter.available() else "unconfigured",
            "stub": "enabled" if config.DEV_MODE else "disabled",
        },
        "classifiers": classify_mod.loaded_classifiers(),
    }


@app.post("/v1/generate", dependencies=[Depends(internal_only)])
async def generate(body: GenerateRequest):
    if not body.stream:
        # Collected mode for machine callers (tier-2 extraction, batch jobs).
        chunks, meta = [], {}
        async for kind, payload in router.generate(body.prompt, body.system, body.max_tokens, body.small_model):
            if kind == "token":
                chunks.append(payload)
            else:
                meta = payload
        return {"text": "".join(chunks), **meta}

    async def stream():
        try:
            async for kind, payload in router.generate(body.prompt, body.system, body.max_tokens, body.small_model):
                if kind == "token":
                    yield sse_event(EVENT_TOKEN, {"text": payload})
                else:
                    yield sse_event(EVENT_DONE, payload)
        except Exception as exc:
            yield sse_event("error", {"error": {"code": "generation_failed", "message": str(exc)}})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/v1/classify", dependencies=[Depends(internal_only)])
async def classify(body: ClassifyRequest):
    return await classify_mod.classify(body.text, body.labels, body.task)


@app.post("/v1/embed", dependencies=[Depends(internal_only)])
async def embed(body: EmbedRequest):
    embeddings, backend = await router.embed(body.texts)
    return {"embeddings": embeddings, "backend": backend, "dim": len(embeddings[0]) if embeddings else 0}
