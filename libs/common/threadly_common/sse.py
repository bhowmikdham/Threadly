"""SSE event framing — the frontend contract. Event names are listed in contracts/CONTRACTS.md."""
import json

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

EVENT_META = "meta"
EVENT_TOKEN = "token"
EVENT_DRAFT = "draft"
EVENT_ENTITIES = "entities"
EVENT_SUMMARY = "summary"
EVENT_RESULTS = "results"
EVENT_ERROR = "error"
EVENT_DONE = "done"


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
