"""Bounded reference binding. Never infer visible order from backend chronology."""

import re

from app.api.errors import ApiError
from app.assistant import routing
from app.assistant.summary import digest
from app.assistant.ui_context import CAPTURE_POLICY
from app.schemas.assistant import RouteDecision, RouteParameters
from app.schemas.ui_context import UIMessageMap

RELEASE = "ui-context-task-1.1.0"
ORDINALS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
)
REFERENCE = (
    r"\b(?P<position>" + "|".join(ORDINALS) + r"|\d+(?:st|nd|rd|th)|selected|this)\s+"
    r"(?P<kind>message|email|thread|option)\b"
)
LOOKUP = (
    r"(?:please\s+)?(?:show(?:\s+me)?|read(?:\s+me)?|what(?:'s| is)\s+in)\s+(?:the\s+)?"
    r"{reference}[?.!]?"
    r"|what\s+does\s+(?:the\s+)?{reference}\s+say[?.!]?"
)
SUMMARISE = r"(?:please\s+)?summari[sz]e\s+(?:the\s+)?{reference}[?.!]?"
# Bounded factual follow-ups, never a command splitter. Mixed/imperative requests
# remain unsupported here and belong in the reviewed master workflow.
FACT_QUESTION = r"^(?:what|which|who|when|where|how (?:much|many|long))\b"
COMMAND_OR_COMPOUND = (
    r"[;\n]|\b(?:then|also|send|forward|reply|draft|compose|schedule|book|delete|"
    r"archive|rewrite|summari[sz]e|ignore|instead)\b"
    r"|\band\s+(?:what|which|who|when|where|how|can|could|please)\b"
)
SUMMARY_REQUEST = "Summarise the single captured message supplied below."


def contract_hash() -> str:
    return digest(
        {
            "release": RELEASE,
            "reference": REFERENCE,
            "lookup": LOOKUP,
            "summarise": SUMMARISE,
            "summary_request": SUMMARY_REQUEST,
            "fact_question": FACT_QUESTION,
            "command_or_compound": COMMAND_OR_COMPOUND,
            "capture_policy": CAPTURE_POLICY,
            "map_schema": UIMessageMap.model_json_schema(),
            "binding_policy": "one-message:no-cross-surface:no-compound:exact-source-quote-v1",
        }
    )


def wrap_release(base: dict) -> dict:
    return {"workflow": RELEASE, "base_release": base, "contract_hash": contract_hash()}


def unwrap_release(release: dict) -> dict:
    if set(release) != {"workflow", "base_release", "contract_hash"} or release != wrap_release(
        release["base_release"]
    ):
        raise ApiError(503, "release_unavailable", "The saved UI reference release is unavailable.")
    return release["base_release"]


def bind_reference(instruction: str, snapshot: dict | None) -> dict | None:
    instruction = " ".join(instruction.casefold().replace("’", "'").split())
    references = list(re.finditer(REFERENCE, instruction))
    if not references:
        return None
    # 'this thread' retains the existing one-thread semantics, not a selected message.
    if len(references) == 1 and references[0].group(0) == "this thread":
        return None
    if len(references) != 1:
        return {"status": "needs_clarification", "reason": "reference_mapping"}
    reference = references[0]
    position, kind = reference.group("position", "kind")
    if kind not in {"message", "email"} or not snapshot or "ui_map" not in snapshot:
        return {"status": "needs_clarification", "reason": "reference_mapping"}
    mapping = UIMessageMap.model_validate(snapshot["ui_map"])
    if position in {"selected", "this"}:
        if len(mapping.selected_message_ids) != 1:
            return {"status": "needs_clarification", "reason": "message_selection"}
        message_id = mapping.selected_message_ids[0]
    else:
        ordinal = ORDINALS.index(position) + 1 if position in ORDINALS else int(position[:-2])
        if ordinal < 1 or ordinal > len(mapping.visible_message_ids):
            return {"status": "needs_clarification", "reason": "reference_mapping"}
        message_id = mapping.visible_message_ids[ordinal - 1]
    message = next((m for m in snapshot["messages"] if m["message_id"] == message_id), None)
    if message is None or not message["body"].strip():
        return {"status": "needs_clarification", "reason": "message_text"}
    literal = re.escape(reference.group(0))
    mode = (
        "lookup"
        if re.fullmatch(LOOKUP.format(reference=literal), instruction)
        else "summary"
        if re.fullmatch(SUMMARISE.format(reference=literal), instruction)
        else "answer"
        if re.search(FACT_QUESTION, instruction) and not re.search(COMMAND_OR_COMPOUND, instruction)
        else None
    )
    if mode is None:
        return {"status": "unsupported", "reason": "reference_operation_not_available"}
    return {
        "status": "ready",
        "mode": mode,
        "message_id": message_id,
        "source_version": digest(message),
        "map_hash": digest(snapshot["ui_map"]),
    }


def reference_route(binding: dict, context_id: str) -> dict:
    status = binding["status"]
    summary = binding.get("mode") == "summary"
    decision = RouteDecision(
        schema_version="1.0",
        status=status,
        intent="summarise" if summary else "other",
        output_kind="clarification"
        if status == "needs_clarification"
        else "summary"
        if summary
        else "answer",
        # Exact extraction is a backend rule, not a model-selected lookup tool.
        operations=["summarise_thread"]
        if summary
        else ["lookup_entity"]
        if binding.get("mode") == "answer"
        else [],
        context_snapshot_id=context_id,
        parameters=RouteParameters.empty(),
        missing_fields=[binding["reason"]] if status == "needs_clarification" else [],
        clarification=(
            "Capture this view and select one accessible message with source text. "
            "Message positions cannot identify threads or meeting options."
            if status == "needs_clarification"
            else None
        ),
        rationale="Resolve one message against the saved UI map; never expand its scope.",
        requested_action="none",
    )
    return {
        "decision": decision.model_dump(),
        "source": "rule",
        "provenance": None,
        "router_version": RELEASE,
        "release": RELEASE,
        "reference_binding": binding,
    }


async def route_request(instruction, hint, context_id, snapshot, model=None, *, draft_input=None):
    binding = bind_reference(instruction, snapshot)
    if binding is not None:
        return reference_route(binding, context_id)
    return await routing.route_request(
        instruction, hint, context_id, snapshot, model, draft_input=draft_input
    )


def validate_binding(route: dict, instruction: str, context_id: str, snapshot: dict) -> dict | None:
    binding = bind_reference(instruction, snapshot)
    if binding is not None and route != reference_route(binding, context_id):
        raise ValueError("Saved reference route does not match its original context")
    if binding is None and "reference_binding" in route:
        raise ValueError("Unexpected reference binding")
    return binding


def scoped_snapshot(snapshot: dict, binding: dict) -> dict:
    messages = [m for m in snapshot["messages"] if m["message_id"] == binding["message_id"]]
    if len(messages) != 1 or digest(messages[0]) != binding["source_version"]:
        raise ValueError("Bound message changed")
    return {
        **snapshot,
        "messages": messages,
        "omitted_messages": snapshot["total_synced_messages"] - 1,
        "truncated_messages": int(binding["message_id"] in snapshot["truncated_message_ids"]),
    }


def lookup_artifact(context_id: str, snapshot: dict, binding: dict) -> dict:
    message = scoped_snapshot(snapshot, binding)["messages"][0]
    quote = message["body"]
    return {
        "schema_version": "1.0",
        "kind": "answer",
        "context_snapshot_id": context_id,
        "coverage": "selection_only",
        "assumptions": [
            "Exact saved cleaned excerpt from one message; not a live mailbox check.",
            "Position refers to the saved UI view, not the current screen.",
            "This excerpt is truncated."
            if message["message_id"] in snapshot["truncated_message_ids"]
            else "Other messages and attachments were not included in this answer.",
        ],
        "evidence": [
            {
                "ref_id": "source-1",
                "source_kind": "message",
                "source_id": message["message_id"],
                "source_version": digest(message),
                "quote": quote,
            }
        ],
        "content": {
            "text": quote,
            "claims": [{"text": quote, "evidence_ref_ids": ["source-1"]}],
            "search_scope": "saved_ui_message",
        },
    }
