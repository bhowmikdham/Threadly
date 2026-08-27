"""Model backends and the fallback chain: Ollama (Mac over Tailscale) is
primary, OpenRouter is the fallback, and a deterministic stub keeps the whole
stack runnable in dev with no models at all."""
import hashlib
import json
import logging
import math
import time
from collections.abc import AsyncIterator

import httpx

from . import config, pii

log = logging.getLogger(__name__)

http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))


class OllamaBackend:
    name = "ollama"

    def __init__(self):
        self._healthy_until = 0.0
        self._healthy = False

    async def healthy(self) -> bool:
        if not config.OLLAMA_BASE_URL:
            return False
        now = time.monotonic()
        if now < self._healthy_until:
            return self._healthy
        try:
            resp = await http.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=config.OLLAMA_HEALTH_TIMEOUT_SECONDS)
            self._healthy = resp.status_code == 200
        except Exception:
            self._healthy = False
        self._healthy_until = now + config.OLLAMA_HEALTH_CACHE_SECONDS
        if not self._healthy:
            log.warning("ollama unhealthy; falling back")
        return self._healthy

    def model(self, small: bool) -> str:
        return config.OLLAMA_SMALL_MODEL if small else config.OLLAMA_MODEL

    async def generate(self, prompt: str, system: str | None, max_tokens: int, small: bool) -> AsyncIterator[str]:
        payload = {
            "model": self.model(small),
            "prompt": prompt,
            "system": system or "",
            "stream": True,
            # thinking OFF + hard cap: keeps the 4b snappy on the Mac
            "think": False,
            "options": {"num_predict": max_tokens},
        }
        async with http.stream("POST", f"{config.OLLAMA_BASE_URL}/api/generate", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if token := data.get("response"):
                    yield token
                if data.get("done"):
                    return

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            resp = await http.post(
                f"{config.OLLAMA_BASE_URL}/api/embeddings",
                json={"model": config.OLLAMA_EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            out.append(resp.json()["embedding"])
        return out


class OpenRouterBackend:
    name = "openrouter"

    def available(self) -> bool:
        return bool(config.OPENROUTER_API_KEY)

    def model(self, small: bool) -> str:
        return config.OPENROUTER_MODEL

    async def generate(self, prompt: str, system: str | None, max_tokens: int, small: bool) -> AsyncIterator[str]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model(small), "messages": messages, "max_tokens": max_tokens, "stream": True}
        headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
        async with http.stream(
            "POST", f"{config.OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[len("data: "):]
                if chunk.strip() == "[DONE]":
                    return
                delta = json.loads(chunk)["choices"][0].get("delta", {})
                if token := delta.get("content"):
                    yield token


class StubBackend:
    """Deterministic dev backend so the E2E cycle runs with zero models."""
    name = "stub"

    def model(self, small: bool) -> str:
        return "stub"

    async def generate(self, prompt: str, system: str | None, max_tokens: int, small: bool) -> AsyncIterator[str]:
        lower = prompt.lower()
        if "summarise" in lower or "summarize" in lower:
            text = (
                "- [stub summary] This thread is a placeholder summary produced without a model.\n"
                "- Configure THREADLY_OLLAMA_BASE_URL or OPENROUTER_API_KEY for real output."
            )
        elif "write an email" in lower or "draft" in lower:
            text = (
                "Hi,\n\n[stub draft] This is a placeholder email body generated without a model. "
                "The draft lifecycle (edit, approve, send) works end-to-end regardless.\n\nBest,\nThreadly"
            )
        else:
            text = "[stub answer] No model is configured, so this is a placeholder response."
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Deterministic pseudo-embeddings: stable across runs, unit-normalised.
        out = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            vec = []
            for i in range(config.EMBED_DIM):
                b = seed[(i * 7) % len(seed)]
                vec.append(math.sin(b + i) )
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class ModelRouter:
    def __init__(self):
        self.ollama = OllamaBackend()
        self.openrouter = OpenRouterBackend()
        self.stub = StubBackend()

    async def pick(self):
        if await self.ollama.healthy():
            return self.ollama
        if self.openrouter.available():
            return self.openrouter
        if config.DEV_MODE:
            return self.stub
        raise RuntimeError("no inference backend available (Ollama down, no OpenRouter key, dev mode off)")

    async def generate(self, prompt: str, system: str | None, max_tokens: int, small: bool):
        """Yields ("token", str) then ("done", metadata). Masks PII when the
        chosen backend leaves the private network (OpenRouter)."""
        backend = await self.pick()
        mapping: dict[str, str] = {}
        if backend.name == "openrouter":
            prompt, mapping = pii.mask(prompt)
            if system:
                system, _ = pii.mask(system)
        max_tokens = min(max_tokens, config.MAX_TOKENS_CAP)

        try:
            async for token in backend.generate(prompt, system, max_tokens, small):
                yield "token", token
        except Exception:
            log.exception("backend %s failed mid-generation", backend.name)
            # One retry on the next backend in the chain.
            fallback = None
            if backend.name == "ollama" and self.openrouter.available():
                fallback = self.openrouter
                prompt, mapping = pii.mask(prompt)
            elif config.DEV_MODE and backend.name != "stub":
                fallback = self.stub
            if fallback is None:
                raise
            backend = fallback
            async for token in backend.generate(prompt, system, max_tokens, small):
                yield "token", token

        yield "done", {"backend": backend.name, "model": backend.model(small), "pii_masked": bool(mapping)}

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        if await self.ollama.healthy():
            try:
                return await self.ollama.embed(texts), "ollama"
            except Exception:
                log.exception("ollama embeddings failed")
        if config.DEV_MODE:
            return await self.stub.embed(texts), "stub"
        raise RuntimeError("no embedding backend available")


router = ModelRouter()
