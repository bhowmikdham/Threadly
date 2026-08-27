"""Client for the inference service. Generation comes back as SSE; this
parses it into {"event": ..., "data": ...} dicts for the orchestrator."""
import json
from collections.abc import AsyncIterator

import httpx

from . import config

http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))


def _headers() -> dict:
    return {"X-Threadly-Internal": config.INTERNAL_TOKEN}


async def generate_stream(
    prompt: str, system: str | None = None, max_tokens: int = 512, small_model: bool = False
) -> AsyncIterator[dict]:
    async with http.stream(
        "POST",
        f"{config.INFERENCE_URL}/v1/generate",
        json={"prompt": prompt, "system": system, "max_tokens": max_tokens, "small_model": small_model},
        headers=_headers(),
    ) as resp:
        resp.raise_for_status()
        event = "message"
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                yield {"event": event, "data": json.loads(line[len("data: "):])}


async def classify(text: str, labels: list[str] | None = None) -> dict:
    resp = await http.post(
        f"{config.INFERENCE_URL}/v1/classify",
        json={"text": text, "labels": labels},
        headers=_headers(),
    )
    resp.raise_for_status()
    return resp.json()


async def embed(texts: list[str]) -> list[list[float]]:
    resp = await http.post(f"{config.INFERENCE_URL}/v1/embed", json={"texts": texts}, headers=_headers())
    resp.raise_for_status()
    return resp.json()["embeddings"]
