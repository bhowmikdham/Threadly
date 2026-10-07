"""Bounded read-only inference retries; no model fallback or output repair."""

import asyncio
import copy
import json
import logging
import random
import threading
import time
from contextlib import suppress
from functools import lru_cache

from app.api.errors import ApiError
from app.classification.limits import get_limits
from app.config import get_settings
from app.pii.masking import mask_structure
from app.workflows.bedrock_flows import FlowError, FlowInvoker

log = logging.getLogger("uvicorn.error")
RETRYABLE_CODES = {"ThrottlingException", "InternalServerException", "ServiceUnavailableException",
                   "ModelTimeoutException", "ServiceQuotaExceededException"}
SAFE_CODES = RETRYABLE_CODES | {
    "AccessDeniedException", "ValidationException", "ResourceNotFoundException",
    "throttlingException", "internalServerException", "badGatewayException",
    "serviceQuotaExceededException", "accessDeniedException", "resourceNotFoundException",
    "validationException", "dependencyFailedException", "EndpointConnectionError",
    "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError",
}
SAFE_OPERATIONS = {"InvokeFlow", "InvokeFlowStream", "GetFlowAlias", "GetFlowVersion",
                   "GetPrompt", "Converse"}


def failure_info(error):
    if isinstance(error, FlowError):
        retryable = error.retryable and error.code == "workflow_upstream_unavailable"
        code, operation = error.provider_code, error.operation
    else:
        response = getattr(error, "response", {})
        detail = response.get("Error", {}) if isinstance(response, dict) else {}
        code = detail.get("Code") if isinstance(detail, dict) else None
        code = code or type(error).__name__
        operation = getattr(error, "operation_name", "unknown")
        retryable = code in RETRYABLE_CODES or code in {
            "EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
            "ConnectionClosedError",
        }
    return retryable, code if code in SAFE_CODES else "unknown", (
        operation if operation in SAFE_OPERATIONS else "unknown")


def runtime_client(region, timeout):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime", region_name=region,
        config=Config(connect_timeout=5, read_timeout=timeout,
                      retries={"mode": "adaptive", "total_max_attempts": 1}),
    )


class ClassificationProvider:
    def __init__(self, factory=runtime_client, *, max_concurrency=2, flow_factory=None,
                 limits=None):
        self.factory = factory
        self.flow_factory = flow_factory
        self.limits = limits
        # Held in the SDK worker, even if its asyncio caller disconnects/cancels.
        self.slots = threading.BoundedSemaphore(max_concurrency)

    def _retry(self, call, stopped, deadline):
        for attempt in range(1, 4):
            if stopped.is_set() or time.monotonic() >= deadline:
                raise ApiError(503, "classification_cancelled", "Classification cancelled.")
            if self.limits is not None:
                self.limits.check_rate(consume=True)
            try:
                return call()
            except ApiError:
                raise
            except Exception as error:
                retryable, code, operation = failure_info(error)
                # Only allowlisted enums; never messages, prompts, IDs or tracebacks.
                log.warning("classification_provider_failure operation=%s code=%s "
                            "retryable=%s attempt=%d", operation, code, retryable, attempt)
                if not retryable or attempt == 3:
                    raise
                delay = min(2 ** (attempt - 1) + random.uniform(0, 1),
                            max(0, deadline - time.monotonic()))
                if stopped.wait(delay):
                    raise ApiError(503, "classification_cancelled",
                                   "Classification cancelled.") from None

    def _call(self, system, payload, *, model, region, timeout, stopped, flow=None):
        if stopped.is_set():
            raise ApiError(503, "classification_cancelled", "Classification cancelled.")
        if not self.slots.acquire(blocking=False):
            raise ApiError(429, "classification_busy", "Try classification again later.",
                           headers={"Retry-After": "5"})
        client = None
        deadline = time.monotonic() + timeout
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
                return self._retry(lambda: invoker._invoke(flow, serialized, stopped).text,
                                   stopped, deadline)
            client = self.factory(region, timeout)
            response = self._retry(lambda: client.converse(
                modelId=model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [
                    {"text": serialized}
                ]}],
                inferenceConfig={"maxTokens": 1500},
            ), stopped, deadline)
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
                           "Classification Flow unavailable.",
                           headers={"Retry-After": "5"}) from None
        except Exception:
            # SDK/transport exceptions can contain prompts and private mail.
            raise ApiError(503, "classification_provider_unavailable",
                           "Classification provider unavailable.",
                           headers={"Retry-After": "5"}) from None
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
    return ClassificationProvider(max_concurrency=get_settings().classification_max_concurrency,
                                  limits=get_limits())
