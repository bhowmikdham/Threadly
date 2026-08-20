"""Module 8 — MODEL CLIENT (build: W1).

One entrypoint for all generation. Owns the fallback chain:

    2s health probe on the Mac -> healthy? use ollama (model_main / model_small)
                               -> down?    use openrouter (model_fallback), input PII-masked

Also owns the guardrails: thinking OFF everywhere, hard token caps per call
site, and per-provider timeouts. Callers never talk to a provider directly.
"""
from app.config import get_settings
from app.model_client.providers import OllamaProvider, OpenRouterProvider


class ModelClient:
    def __init__(self) -> None:
        self.ollama = OllamaProvider()
        self.openrouter = OpenRouterProvider()

    async def pick_provider(self):
        settings = get_settings()
        if await self.ollama.health(settings.model_health_timeout_s):
            return self.ollama
        return self.openrouter

    async def generate(self, prompt: str, *, small: bool = False, **caps) -> str:
        """TODO(W1): route to provider; mask via app.pii before any cloud egress;
        record which provider served (needed for the ablation study)."""
        raise NotImplementedError

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        """TODO(W3): constrained JSON for planner/extractor tier-2 (model_small)."""
        raise NotImplementedError
