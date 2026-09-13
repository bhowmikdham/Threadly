"""Run with python -m app.assistant.worker [--once]. No API-process background tasks."""

import argparse
import asyncio
import logging

from app.assistant.summary import make_artifact, make_prompt, release_manifest
from app.assistant.tasks import claim_next, finish
from app.db.engine import get_engine, get_session_factory
from app.model_client.client import get_model_client
from app.model_client.providers import ProviderError

log = logging.getLogger("threadly.assistant.worker")
GENERATION_TIMEOUT_SECONDS = 120  # shorter than the 180-second fenced lease


async def run_once(factory=None, model=None) -> bool:
    factory = factory or get_session_factory()
    async with factory.begin() as session:
        claim = await claim_next(session)
    if claim is None:
        return False
    payload, provenance, error, retryable = None, None, None, False
    if claim.release != release_manifest():
        error = "release_unavailable"
    else:
        try:
            async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
                text, info = await (model or get_model_client()).generate(
                    make_prompt(claim.snapshot), max_tokens=1800
                )
            payload = make_artifact(text, claim.context_id, claim.snapshot)
            provenance = {"provider": info.provider, "model": info.model, "release": claim.release}
        except (ProviderError, TimeoutError):
            error, retryable = "upstream_model_unavailable", True
        except (ValueError, TypeError):
            error = "invalid_summary_output"
        except Exception:
            # Never log source content, prompt text or provider error bodies.
            error = "generation_failed"
    async with factory.begin() as session:
        await finish(
            session,
            claim,
            payload=payload,
            provenance=provenance,
            error_code=error,
            retryable=retryable,
        )
    return True


async def serve(once: bool = False):
    try:
        while True:
            try:
                worked = await run_once()
            except Exception as exc:
                log.error(
                    "Worker transaction failed (%s); lease recovery will retry", type(exc).__name__
                )
                if once:
                    raise
                worked = False
            if once:
                break
            if not worked:
                await asyncio.sleep(2)
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(args.once))
    except KeyboardInterrupt:
        pass
