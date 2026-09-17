"""One bounded Gmail write; no retries, redirects or model-generated arguments."""

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import dataclass

import httpx

from app.actions import email_payload, service
from app.config import get_settings
from app.model_client.structured import reject_duplicate_keys

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# General rollout remains closed; explicit pilot enrollment is checked separately.
RECOVERY_READY = False
ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def enabled(transport=None, *, user_id=None):
    return get_settings().email_writes_enabled and (
        RECOVERY_READY
        or isinstance(transport, httpx.MockTransport)
        or str(user_id) in get_settings().write_pilot_user_ids_values
    )


@dataclass(frozen=True)
class Outcome:
    state: str
    code: str
    message_id: str | None = None
    thread_id: str | None = None

    def evidence(self):
        return {
            "state": self.state,
            "code": self.code,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
        }


def frozen_request(action):
    payload = action.payload
    if (
        action.payload_schema != email_payload.SCHEMA
        or service.candidate_hash(action.payload_schema, payload) != action.payload_hash
    ):
        raise ValueError("Invalid saved email payload")
    try:
        raw = payload["mime_base64url"]
        if not isinstance(raw, str) or len(raw) > 86000:
            raise ValueError
        decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
        if (
            not decoded
            or len(decoded) > 64000
            or hashlib.sha256(decoded).hexdigest() != payload["mime_sha256"]
        ):
            raise ValueError
        result = {"raw": raw}
        thread = payload["preview"]["gmail_thread_id"]
        if thread is not None:
            if not isinstance(thread, str) or not ID.fullmatch(thread):
                raise ValueError
            result["threadId"] = thread
        return result
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid saved email payload") from None


async def send(token, request, *, transport=None, user_id=None):
    if not enabled(transport, user_id=user_id):
        return Outcome("failed", "writes_disabled_before_http")
    try:
        async with asyncio.timeout(35):
            async with httpx.AsyncClient(
                transport=transport, timeout=30, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "POST", SEND_URL, headers={"Authorization": f"Bearer {token}"}, json=request
                ) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        data.extend(chunk)
                        if len(data) > 16384:
                            return Outcome("outcome_unknown", "response_too_large")
                    try:
                        body = json.loads(data, object_pairs_hook=reject_duplicate_keys)
                    except (ValueError, UnicodeError):
                        return Outcome("outcome_unknown", "invalid_response")
                    if not isinstance(body, dict):
                        return Outcome("outcome_unknown", "invalid_response")
                    if response.status_code == 200:
                        mid, tid = body.get("id"), body.get("threadId")
                        if (
                            isinstance(mid, str)
                            and ID.fullmatch(mid)
                            and isinstance(tid, str)
                            and ID.fullmatch(tid)
                            and ("threadId" not in request or request["threadId"] == tid)
                        ):
                            return Outcome("succeeded", "gmail_accepted", mid, tid)
                        return Outcome("outcome_unknown", "invalid_success")
                    # Narrow documented rejections only. Rate limits and server errors
                    # stay unknown; no generic retry policy is safe for sends.
                    error = body.get("error")
                    if isinstance(error, dict) and error.get("code") == response.status_code:
                        reasons = error.get("errors")
                        allowed = {
                            400: {"badRequest"},
                            401: {"authError"},
                            403: {"domainPolicy", "insufficientPermissions", "forbidden"},
                        }
                        if (
                            response.status_code in allowed
                            and isinstance(reasons, list)
                            and reasons
                            and all(
                                isinstance(r, dict)
                                and r.get("reason") in allowed[response.status_code]
                                for r in reasons
                            )
                        ):
                            return Outcome("failed", f"gmail_rejected_{response.status_code}")
                    return Outcome("outcome_unknown", "unconfirmed_http_response")
    except (httpx.HTTPError, TimeoutError):
        return Outcome("outcome_unknown", "transport_uncertain")
