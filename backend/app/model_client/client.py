"""Module 8 — MODEL CLIENT (W1, implemented).

One entrypoint for all generation. Owns:
- the fallback chain: 2s health probe on the Mac -> ollama, else openrouter
- PII masking before ANY cloud egress (module 9's rule, enforced here)
- hard token caps + thinking OFF (providers)
- provider bookkeeping (the ablation study needs to know who served what)
"""
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.config import get_settings
from app.model_client.providers import OllamaProvider, OpenRouterProvider, ProviderError
from app.pii.masking import mask

log = logging.getLogger("threadly.model")

_HEALTH_CACHE_S = 15.0  # don't pay the probe on every call


@dataclass
class GenResult:
    provider: str
    model: str


class ModelClient:
    def __init__(
        self,
        ollama: OllamaProvider | None = None,
        openrouter: OpenRouterProvider | None = None,
    ):
        self.ollama = ollama or OllamaProvider()
        self.openrouter = openrouter or OpenRouterProvider()
        self._mac_ok: bool | None = None
        self._mac_checked = 0.0

    async def _mac_healthy(self) -> bool:
        now = time.monotonic()
        if self._mac_ok is None or now - self._mac_checked > _HEALTH_CACHE_S:
            self._mac_ok = await self.ollama.health(get_settings().model_health_timeout_s)
            self._mac_checked = now
        return self._mac_ok

    async def stream(
        self, prompt: str, *, small: bool = False, max_tokens: int = 700
    ) -> tuple[AsyncIterator[str], GenResult]:
        """Pick a provider, return (token iterator, bookkeeping). Falls back to
        openrouter mid-decision (not mid-stream) when the Mac probe fails."""
        settings = get_settings()
        if await self._mac_healthy():
            model = settings.model_small if small else settings.model_main
            try:
                return self.ollama.stream(prompt, model=model, max_tokens=max_tokens), GenResult(
                    "ollama", model
                )
            except ProviderError:
                log.warning("ollama refused despite healthy probe — falling back")
                self._mac_ok = False

        # Cloud egress: mask BEFORE the payload leaves the box (placeholders only).
        masked_prompt, _mapping = mask(prompt)
        model = settings.model_fallback
        return self.openrouter.stream(masked_prompt, model=model, max_tokens=max_tokens), GenResult(
            "openrouter", model
        )

    async def generate(
        self, prompt: str, *, small: bool = False, max_tokens: int = 700
    ) -> tuple[str, GenResult]:
        it, info = await self.stream(prompt, small=small, max_tokens=max_tokens)
        parts = [t async for t in it]
        return "".join(parts), info


_client: ModelClient | None = None


def get_model_client() -> ModelClient:
    global _client
    if _client is None:
        _client = ModelClient()
    return _client
