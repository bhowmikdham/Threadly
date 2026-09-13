"""Run with python -m app.assistant.worker [--once]. No API-process background tasks."""

import argparse
import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import ApiError
from app.assistant import routing
from app.assistant.summary import make_artifact, make_prompt, release_manifest
from app.assistant.tasks import claim_next, finish, save_route
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
    stopped_state = None
    legacy = claim.release == release_manifest()
    if not legacy and claim.release != routing.release_manifest():
        error = "release_unavailable"
    else:
        stage = "routing" if not legacy else "summary"
        try:
            # One timeout bounds routing + checkpoint + generation inside the same lease.
            async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
                route = claim.route
                if not legacy:
                    if route is None:
                        route = await routing.route_request(
                            claim.instruction,
                            claim.intent_hint,
                            claim.context_id,
                            claim.snapshot,
                            model,
                        )
                        async with factory.begin() as session:
                            if not await save_route(session, claim, route):
                                return True  # cancelled, deleted or replaced during classification
                    outcome, error = routing.dispatch_outcome(route, claim.snapshot)
                    if outcome in {"needs_clarification", "unsupported"}:
                        stopped_state = outcome
                if not stopped_state and not error:
                    stage = "summary"
                    prompt = (
                        make_prompt(claim.snapshot)
                        if legacy
                        else routing.summary_prompt(claim.snapshot, claim.instruction)
                    )
                    text, info = await (model or get_model_client()).generate(
                        prompt, max_tokens=1800
                    )
                    payload = make_artifact(text, claim.context_id, claim.snapshot)
                    provenance = {
                        "provider": info.provider,
                        "model": info.model,
                        "release": claim.release,
                    }
        except ApiError as exc:
            error, retryable = exc.code, exc.code == "upstream_model_unavailable"
        except (ProviderError, TimeoutError):
            error, retryable = "upstream_model_unavailable", True
        except (ValueError, TypeError):
            error = "invalid_route_output" if stage == "routing" else "invalid_summary_output"
        except SQLAlchemyError:
            raise  # Checkpoint failure belongs to durable lease recovery, not model failure.
        except Exception:
            # Never log source content, prompt text or provider error bodies.
            error = "routing_failed" if stage == "routing" else "generation_failed"
    async with factory.begin() as session:
        await finish(
            session,
            claim,
            payload=payload,
            provenance=provenance,
            error_code=error,
            retryable=retryable,
            stopped_state=stopped_state,
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
