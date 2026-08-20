"""Inference providers (module 8). Two, by design (ADR 001):

- OllamaProvider — the Mac over Tailscale. PRIMARY. Payloads are PII-masked
  upstream of this call only when leaving to CLOUD providers; tailscale traffic
  is private but we mask anyway when the payload later feeds a cloud fallback.
- OpenRouterProvider — stock qwen3.5-9b. Fallback when the Mac is down, plus
  long-thread summaries and the ablation Tier-1 baseline. ALWAYS masked.
"""
import httpx

from app.config import get_settings


class OllamaProvider:
    name = "ollama"

    async def health(self, timeout_s: float) -> bool:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                r = await client.get(f"{settings.ollama_base_url}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate(self, model: str, prompt: str, **caps) -> str:
        """TODO(W1): POST /api/generate, stream=False first, thinking OFF, num_predict cap."""
        raise NotImplementedError


class OpenRouterProvider:
    name = "openrouter"

    async def health(self, timeout_s: float) -> bool:
        return bool(get_settings().openrouter_api_key)

    async def generate(self, model: str, prompt: str, **caps) -> str:
        """TODO(W1): chat/completions with max_tokens cap. Input MUST already be PII-masked."""
        raise NotImplementedError
