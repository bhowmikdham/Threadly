"""Bounded Converse/visual Flow calls. No fallback or application retries."""

import asyncio
import copy
import json
import threading
from contextlib import suppress
from functools import lru_cache

from app.api.errors import ApiError
from app.config import get_settings
from app.pii.masking import mask_structure
from app.workflows.bedrock_flows import FlowError, FlowInvoker


def runtime_client(region, timeout):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(connect_timeout=5, read_timeout=timeout,
                      retries={"mode": "adaptive", "total_max_attempts": 1}),
    )


class ClassificationProvider:
    def __init__(self, factory=runtime_client, *, max_concurrency=2, flow_factory=None):
        self.factory = factory
        self.flow_factory = flow_factory
        # Held in the SDK worker, even if its asyncio caller disconnects/cancels.
        self.slots = threading.BoundedSemaphore(max_concurrency)

    def _call(self, system, payload, *, model, region, timeout, stopped, flow=None):
        if stopped.is_set():
            raise ApiError(503, "classification_cancelled", "Classification cancelled.")
        if not self.slots.acquire(blocking=False):
            raise ApiError(429, "classification_busy", "Try classification again later.",
                           headers={"Retry-After": "5"})
        client = None
        try:
            if stopped.is_set():
                raise ApiError(503, "classification_cancelled", "Classification cancelled.")
            # Only mail text needs masking. Keep backend timestamps/source IDs and
            # participant aliases exact; broad phone patterns can corrupt dates.
            masked = copy.deepcopy(payload)
            text_fields, _ = mask_structure([
                {"subject": m["subject"], "body": m["body"]} for m in payload["messages"]
            ])
            for message, fields in zip(masked["messages"], text_fields, strict=True):
                message.update(fields)
            serialized = json.dumps(masked, ensure_ascii=True)
            if len(serialized.encode()) > 64000:
                raise ApiError(422, "classification_input_limit", "Classification input too large.")
            if flow is not None:
                from app.classification.flows import verify_target

                # Input is already masked field-by-field. Calling invoke() would
                # mask backend dates/source metadata again, corrupting evidence.
                invoker = FlowInvoker(self.flow_factory, target_verifier=verify_target)
                return invoker._invoke(flow, serialized, stopped).text
            client = self.factory(region, timeout)
            response = client.converse(
                modelId=model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [
                    {"text": serialized}
                ]}],
                inferenceConfig={"maxTokens": 1500},
            )
            message = response.get("output", {}).get("message", {})
            blocks = message.get("content")
            if (response.get("stopReason") != "end_turn"
                    or message.get("role") != "assistant"
                    or not isinstance(blocks, list) or not blocks
                    or any(not isinstance(b, dict) or set(b) != {"text"}
                           or not isinstance(b["text"], str) for b in blocks)):
                raise ApiError(502, "classification_output_invalid",
                               "The model did not return a complete classification.")
            text = "".join(b["text"] for b in blocks)
            if not text.strip() or len(text) > 16000:
                raise ApiError(502, "classification_output_invalid", "Invalid model output.")
            return text
        except ApiError:
            raise
        except FlowError as error:
            if error.code == "workflow_release_changed":
                raise ApiError(409, "classification_release_changed",
                               "Classification Flow release changed.") from None
            if error.code in {"invalid_flow_output", "incomplete_flow_output",
                              "workflow_output_limit", "workflow_input_required"}:
                raise ApiError(502, "classification_output_invalid",
                               "The Flow did not return a complete classification.") from None
            raise ApiError(503, "classification_provider_unavailable",
                           "Classification Flow unavailable.") from None
        except Exception:
            # SDK/transport exceptions can contain prompts and private mail.
            raise ApiError(503, "classification_provider_unavailable",
                           "Classification provider unavailable.") from None
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()
            self.slots.release()

    async def generate(self, system, payload, *, model, region, timeout, flow=None):
        stopped = threading.Event()
        try:
            async with asyncio.timeout(timeout + 10):
                return await asyncio.to_thread(
                    self._call, system, payload, model=model, region=region,
                    timeout=timeout, stopped=stopped, flow=flow,
                )
        except TimeoutError:
            raise ApiError(503, "classification_provider_unavailable",
                           "Classification timed out.") from None
        finally:
            stopped.set()


@lru_cache
def get_provider():
    return ClassificationProvider(max_concurrency=get_settings().classification_max_concurrency)
