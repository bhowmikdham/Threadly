"""Inference providers (module 8, W1 — implemented). Two, by design (ADR 001):

- OllamaProvider — the Mac over Tailscale. PRIMARY.
- OpenRouterProvider — fallback when the Mac is down, long threads, ablation
  Tier-1. Cloud egress => caller MUST pass PII-masked input (client.py owns it).

Both stream. Thinking stays OFF (qwen3: think=false / reasoning excluded) and
every call carries a hard output-token cap.
"""
import json
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings


class ProviderError(Exception):
    pass


class OllamaProvider:
    name = "ollama"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def health(self, timeout_s: float) -> bool:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=timeout_s, transport=self._transport) as c:
                r = await c.get(f"{settings.ollama_base_url}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def stream(
        self, prompt: str, *, model: str, max_tokens: int, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        settings = get_settings()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "think": False,  # thinking OFF everywhere (latency + cost, module 8 rule)
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                async with c.stream(
                    "POST", f"{settings.ollama_base_url}/api/generate", json=payload
                ) as r:
                    if r.status_code != 200:
                        raise ProviderError(f"ollama {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("response"):
                            yield chunk["response"]
                        if chunk.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama transport: {exc}") from exc


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def health(self, timeout_s: float) -> bool:
        return bool(get_settings().openrouter_api_key)

    async def stream(
        self, prompt: str, *, model: str, max_tokens: int, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        settings = get_settings()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning": {"exclude": True},
        }
        headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                async with c.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as r:
                    if r.status_code != 200:
                        raise ProviderError(f"openrouter {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line.removeprefix("data: ").strip()
                        if data == "[DONE]":
                            return
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter transport: {exc}") from exc
