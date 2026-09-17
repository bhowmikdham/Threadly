"""Run with python -m app.assistant.worker [--once]. No API-process background tasks."""

import argparse
import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import ApiError
from app.assistant import (
    continuation,
    drafting,
    lookup_draft,
    reads,
    routing,
    routing_v1,
    scheduling,
    steps,
    summary_quality,
    ui_routing,
)
from app.assistant.summary import make_artifact, make_prompt, release_manifest
from app.assistant.tasks import claim_next, finish, save_route
from app.db.engine import get_engine, get_session_factory
from app.model_client.client import get_model_client
from app.model_client.providers import ProviderError
from app.workflows import registry
from app.workflows.bedrock_flows import FlowError, FlowInvoker

log = logging.getLogger("threadly.assistant.worker")
GENERATION_TIMEOUT_SECONDS = 120  # shorter than the 180-second fenced lease


async def run_once(factory=None, model=None, flow_invoker=None) -> bool:
    factory = factory or get_session_factory()
    async with factory.begin() as session:
        claim = await claim_next(session)
    if claim is None:
        return False
    if claim.release.get("workflow") == scheduling.RELEASE:
        await scheduling.run_task(factory, claim)
        return True
    if claim.release.get("workflow") in {steps.RELEASE, lookup_draft.RELEASE}:
        await steps.run_task(factory, claim, model, flow_invoker)
        return True
    if claim.release.get("workflow") == reads.RELEASE:
        await reads.run_task(factory, claim, model)
        return True
    payload, provenance, error, retryable = None, None, None, False
    stopped_state = None
    manifest = None
    release_has_ui = claim.release.get("workflow") == ui_routing.RELEASE
    ui_context = release_has_ui or bool(
        claim.continuation_release and claim.snapshot and "ui_map" in claim.snapshot
    )
    dispatch_release = claim.release
    concise = False
    concise_policy = None
    try:
        if claim.continuation_release is not None:
            continuation.validate_release(claim.continuation_release)
        if release_has_ui:
            dispatch_release = ui_routing.unwrap_release(claim.release)
        concise = dispatch_release.get("workflow") == summary_quality.RELEASE
        if concise:
            concise_policy = summary_quality.policy_for_release(dispatch_release)
            dispatch_release = summary_quality.unwrap_release(dispatch_release)
        if dispatch_release.get("workflow") == registry.RELEASE:
            manifest = registry.pinned_manifest(dispatch_release)
    except ApiError as exc:
        error = exc.code
    legacy = dispatch_release == release_manifest()
    previous = dispatch_release == routing_v1.release_manifest()
    if error or (
        not legacy
        and not previous
        and manifest is None
        and dispatch_release != routing.release_manifest()
    ):
        error = "release_unavailable"
    else:
        stage = "routing" if not legacy else "summary"
        try:
            # One timeout bounds routing + checkpoint + generation inside the same lease.
            async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
                route = claim.route
                binding = None
                snapshot = claim.snapshot
                if not legacy:
                    if route is None:
                        router = ui_routing if ui_context else routing_v1 if previous else routing
                        options = {} if previous else {"draft_input": claim.draft_input}
                        route = await router.route_request(
                            claim.instruction,
                            claim.intent_hint,
                            claim.context_id,
                            claim.snapshot,
                            model,
                            **options,
                        )
                        if claim.continuation_release:
                            route = continuation.resolve_time_context(
                                route,
                                claim.instruction,
                                claim.resolved_inputs or {},
                                claim.snapshot,
                                claim.draft_input,
                            )
                        async with factory.begin() as session:
                            if not await save_route(session, claim, route):
                                return True  # cancelled, deleted or replaced during classification
                    if ui_context:
                        binding = ui_routing.validate_binding(
                            route, claim.instruction, claim.context_id, snapshot
                        )
                    if binding is not None:
                        outcome = binding["status"]
                        error = binding.get("reason") if outcome == "unsupported" else None
                        if claim.draft_input is not None:
                            outcome, error = "unsupported", "draft_options_intent_mismatch"
                    else:
                        outcome, error = (
                            routing_v1.dispatch_outcome(route, snapshot)
                            if previous
                            else routing.dispatch_outcome(route, snapshot, claim.draft_input)
                        )
                    if outcome in {"needs_clarification", "unsupported"}:
                        stopped_state = outcome
                if not stopped_state and not error and binding and binding["mode"] == "lookup":
                    payload = ui_routing.lookup_artifact(claim.context_id, snapshot, binding)
                    provenance = {"provider": "native", "model": None, "release": claim.release}
                if not stopped_state and not error and payload is None:
                    if binding:
                        snapshot = ui_routing.scoped_snapshot(snapshot, binding)
                    is_draft = (
                        not legacy
                        and not previous
                        and route["decision"]["intent"] in {"reply", "compose"}
                    )
                    stage = "draft" if is_draft else "summary"
                    mode = "reply" if is_draft and route["decision"]["intent"] == "reply" else "new"
                    if is_draft:
                        prompt = drafting.make_prompt(
                            claim.instruction, claim.snapshot, claim.draft_input, mode
                        )
                    else:
                        prompt = (
                            make_prompt(claim.snapshot)
                            if legacy
                            else summary_quality.make_prompt(
                                snapshot,
                                ui_routing.SUMMARY_REQUEST if binding else claim.instruction,
                                policy=concise_policy,
                            )
                            if concise
                            else (routing_v1 if previous else routing).summary_prompt(
                                snapshot,
                                ui_routing.SUMMARY_REQUEST if binding else claim.instruction,
                            )
                        )
                    operation = "draft_reply" if mode == "reply" else "draft_new"
                    if not is_draft:
                        operation = "summarise_thread"
                    entry = manifest.operations[operation] if manifest else None
                    if isinstance(entry, registry.FlowEntry):
                        result = await (flow_invoker or FlowInvoker()).invoke(entry, prompt)
                        text = result.text
                        provenance = {**result.provenance, "release": claim.release}
                    else:
                        text, info = await (model or get_model_client()).generate(
                            prompt, max_tokens=2500 if is_draft else 1800
                        )
                        provenance = {
                            "provider": info.provider,
                            "model": info.model,
                            "release": claim.release,
                        }
                    payload = (
                        drafting.make_artifact(text, claim, mode)
                        if is_draft
                        else (summary_quality.make_artifact if concise else make_artifact)(
                            text, claim.context_id, snapshot
                        )
                    )
        except FlowError as exc:
            error, retryable = exc.code, exc.retryable
        except ApiError as exc:
            error, retryable = exc.code, exc.code == "upstream_model_unavailable"
        except (ProviderError, TimeoutError):
            error, retryable = "upstream_model_unavailable", True
        except (ValueError, TypeError):
            error = {
                "routing": "invalid_route_output",
                "draft": "invalid_draft_output",
                "summary": "invalid_summary_output",
            }[stage]
        except SQLAlchemyError:
            raise  # Checkpoint failure belongs to durable lease recovery, not model failure.
        except Exception:
            # Never log source content, prompt text or provider error bodies.
            error = "routing_failed" if stage == "routing" else "generation_failed"
    if payload is not None and provenance is not None and claim.continuation_release:
        provenance = {
            **provenance,
            "continuation_release": claim.continuation_release,
            "input_version": claim.input_version,
        }
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
