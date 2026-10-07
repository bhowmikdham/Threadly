"""Request-local classification: fetch, classify, refetch, return; never persist mail."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.api.errors import ApiError
from app.classification.contracts import ClassificationResponse, Decision, Evidence
from app.classification.flows import FLOW_RELEASE, ClassificationFlow, configured_flow
from app.classification.limits import get_limits
from app.classification.provider import get_provider
from app.config import get_settings
from app.mail import live
from app.model_client.structured import json_object

PROMPT = Path(__file__).with_name("prompt.txt").read_text()
POLICY_VERSION = "1.0.0"
MAX_MESSAGES = 50
MAX_TEXT_CHARS = 24000
SCHEMA = Decision.model_json_schema()


@dataclass(frozen=True)
class Release:
    model: str
    region: str
    timeout: int
    validity: int
    identifier: str
    flow: ClassificationFlow | None = None


def release() -> Release:
    settings = get_settings()
    if not settings.classification_enabled:
        raise ApiError(503, "classification_disabled", "Email classification is not enabled.")
    if (settings.gmail_source_mode != "on_demand"
            or not settings.bedrock_mail_processing_acknowledged):
        raise ApiError(503, "classification_not_configured",
                       "Classification requires on-demand Gmail and a configured model.")
    flow = configured_flow() if settings.classification_transport == "bedrock_flow" else None
    model = flow.model_profile_arn if flow else settings.classification_model_id.strip()
    region = flow.region if flow else settings.bedrock_region
    timeout = flow.timeout_seconds if flow else settings.bedrock_read_timeout_s
    # Managed prompt ARNs cannot accept our explicit system/inference configuration.
    if not model or ":prompt/" in model:
        raise ApiError(503, "classification_not_configured",
                       "Configure a Converse model or inference profile.")
    values = {
        "policy": POLICY_VERSION, "prompt": PROMPT, "schema": SCHEMA,
        "model": model, "region": region,
        "transport": settings.classification_transport,
        "flow": flow.model_dump() if flow else None,
        "flow_release": FLOW_RELEASE if flow else None,
        "validity": settings.classification_valid_seconds,
        "timeout": timeout,
    }
    identifier = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return Release(model, region, timeout, settings.classification_valid_seconds, identifier, flow)


def source_guard(source, owner, account_version, thread_id):
    if (source["owner"] != owner or source["account_version"] != account_version
            or source["thread_id"] != thread_id):
        raise ApiError(409, "classification_source_changed", "Select the current thread again.")


def context(messages, mailbox_address):
    """Preserve whole messages and stable participant roles without exposing addresses."""
    participants = {mailbox_address.casefold(): "ME"}

    def participant(address):
        if address not in participants:
            participants[address] = f"person{len(participants)}"
        return participants[address]

    output = []
    for index, message in enumerate(messages, 1):
        addresses = message["reply_metadata"].get("addresses", {})
        if len(addresses.get("from", [])) != 1:
            return None
        if not message.get("received_at"):
            return None
        output.append({
            "source_id": f"m{index}",
            "from": [participant(a) for a in addresses.get("from", [])],
            "to": [participant(a) for a in addresses.get("to", [])],
            "cc": [participant(a) for a in addresses.get("cc", [])],
            "sent_by_account": bool(message["is_from_user"] or
                                    "SENT" in message["reply_metadata"]["label_ids"]),
            "received_at": message["received_at"],
            "sent_at": message.get("sent_at"),
            "subject": message.get("subject") or "",
            "body": message.get("body_clean") or "",
        })
    return output


def parse_decision(text, source_ids):
    try:
        value = Decision.model_validate(json_object(text, max_chars=16000))
        if value.evidence:
            for refs in value.evidence.model_dump().values():
                if not set(refs) <= set(source_ids):
                    raise ValueError("Unknown evidence")
        return value
    except (ValueError, ValidationError, RecursionError):
        raise ApiError(502, "classification_output_invalid",
                       "The model returned an invalid classification.") from None


async def classify(owner, account_version, mailbox_address, thread_id, request):
    live.identifier(thread_id)
    pinned = release()
    # Reject excess work before fetching Gmail or starting an SDK worker.
    with get_limits().admission():
        return await _classify(owner, account_version, mailbox_address, thread_id, request, pinned)


async def _classify(owner, account_version, mailbox_address, thread_id, request, pinned):
    source = await live.thread(owner, thread_id)
    source_guard(source, owner, account_version, thread_id)
    # A Gmail draft cannot resolve a prior unanswered request.
    messages = [m for m in source["messages"]
                if not set(m["reply_metadata"]["label_ids"]) & {"DRAFT", "SPAM", "TRASH"}]
    evaluated_at = datetime.now(UTC)
    response = dict(
        thread_id=thread_id, source_message_ids=[m["gmail_msg_id"] for m in messages],
        source_fingerprint=source["fingerprint"], labels=None, evidence=None,
        evaluated_at=evaluated_at, valid_until=evaluated_at + timedelta(seconds=pinned.validity),
        time_zone=request.time_zone, release_id=pinned.identifier,
    )
    if not messages or not any("INBOX" in m["reply_metadata"]["label_ids"] for m in messages):
        return ClassificationResponse(**response, status="skipped", reason_codes=["not_in_inbox"])
    size = sum(len(m.get("body_clean") or "") + len(m.get("subject") or "") for m in messages)
    if len(messages) > MAX_MESSAGES or size > MAX_TEXT_CHARS:
        return ClassificationResponse(**response, status="needs_review",
                                      reason_codes=["context_limit"])
    inputs = context(messages, mailbox_address)
    if inputs is None or any(not (m["subject"].strip() or m["body"].strip()) for m in inputs):
        return ClassificationResponse(**response, status="needs_review",
                                      reason_codes=["insufficient_context"])
    source_ids = {f"m{i}": m["gmail_msg_id"] for i, m in enumerate(messages, 1)}
    payload = {"messages": inputs, "time_zone": request.time_zone,
               "evaluated_at": evaluated_at.isoformat(), "output_schema": SCHEMA}
    if len(json.dumps(payload).encode()) > 64000:
        return ClassificationResponse(**response, status="needs_review",
                                      reason_codes=["context_limit"])
    text = await get_provider().generate(
        PROMPT, payload, model=pinned.model, region=pinned.region, timeout=pinned.timeout,
        **({"flow": pinned.flow} if pinned.flow else {}),
    )
    decision = parse_decision(text, source_ids)
    current = await live.thread(owner, thread_id)
    source_guard(current, owner, account_version, thread_id)
    if current["fingerprint"] != source["fingerprint"]:
        raise ApiError(409, "classification_source_changed", "Thread changed; classify it again.")
    if release().identifier != pinned.identifier:
        raise ApiError(409, "classification_release_changed", "Classification release changed.")
    if datetime.now(UTC) >= response["valid_until"]:
        raise ApiError(409, "classification_expired", "Classification expired; try again.")
    if decision.evidence:
        response["evidence"] = Evidence(**{
            field: [source_ids[ref] for ref in refs]
            for field, refs in decision.evidence.model_dump().items()
        })
    response["labels"] = decision.labels
    return ClassificationResponse(**response, status=decision.status,
                                  reason_codes=decision.reason_codes)
