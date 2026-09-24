"""Finite read/decision loop, independent of transport and persistence."""

import asyncio
import json

from pydantic import ValidationError

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.conversation.prompt import PROMPT, RELEASE
from app.model_client.conversation import ConversationModel, ConversationProviderError
from app.model_client.providers import ProviderError
from app.schemas.conversation import TOOLS, Respond, tool_config

TERMINAL = {"respond", "prepare_workflow", "answer_question", "revise_draft"}
MAX_CALLS = 8


async def run(context, runtime, model=None):
    messages = [{"role": "user", "content": [{"text": json.dumps(context)}]}]
    seen, calls, trace = set(), 0, []
    try:
        async with asyncio.timeout(120):
            while calls < MAX_CALLS:
                for attempt in range(3):
                    try:
                        message = await (model or ConversationModel()).decide(
                            PROMPT, messages, tool_config()
                        )
                        break
                    except ConversationProviderError as exc:
                        if (
                            exc.code
                            not in {
                                "ThrottlingException",
                                "ModelTimeoutException",
                                "ServiceUnavailableException",
                                "InternalServerException",
                            }
                            or attempt == 2
                        ):
                            raise
                        await asyncio.sleep(2 ** (attempt + 1))
                blocks = message.get("content", [])
                requests = [b["toolUse"] for b in blocks if "toolUse" in b]
                if not requests or len(requests) > MAX_CALLS - calls:
                    break
                messages.append(message)
                results = []
                for call in requests:
                    calls += 1
                    name, values = call["name"], call["input"]
                    key = digest({"tool": name, "input": values})
                    try:
                        if name not in TOOLS:
                            raise ValueError("Unknown tool")
                        if name in TERMINAL and len(requests) != 1:
                            raise ValueError("Use a terminal tool alone after observations")
                        if key in seen:
                            raise ValueError(
                                "Repeated call; use existing observation or explain limitation"
                            )
                        seen.add(key)
                        arguments = TOOLS[name][0].model_validate(values)
                        if name == "respond":
                            outcome = validate_response(arguments, runtime)
                        else:
                            outcome = await runtime.call(name, arguments)
                        trace.append({"tool": name, "status": "ok"})
                        if name in TERMINAL:
                            return {**outcome, "release": RELEASE, "trace": trace}
                        result = {"json": outcome}
                        status = "success"
                    except (ValidationError, ValueError):
                        trace.append(
                            {"tool": name if name in TOOLS else "unknown", "status": "invalid"}
                        )
                        result = {
                            "json": {
                                "error": "invalid_tool_input",
                                "message": (
                                    "Use declared schema and user-supplied search terms. "
                                    "Use valid references and exact quotes from read_email."
                                ),
                            }
                        }
                        status = "error"
                    except ApiError as exc:
                        trace.append({"tool": name, "status": exc.code})
                        result = {"json": {"error": exc.code, "message": exc.message}}
                        status = "error"
                    results.append(
                        {
                            "toolResult": {
                                "toolUseId": call["toolUseId"],
                                "content": [result],
                                "status": status,
                            }
                        }
                    )
                messages.append({"role": "user", "content": results})
                if len(json.dumps(messages)) > 85000:
                    break
    except ConversationProviderError:
        raise ApiError(
            503,
            "conversation_provider_unavailable",
            "The model is temporarily unavailable. Retry this message.",
        ) from None
    except (ProviderError, TimeoutError):
        raise ApiError(
            503, "conversation_unavailable", "I couldn’t finish that response. Retry this message."
        ) from None
    raise ApiError(
        422,
        "conversation_tool_limit",
        "I reached the limit for this request. Try a smaller question.",
    )


def validate_response(answer: Respond, runtime):
    for source in answer.evidence:
        text = runtime.evidence.get(source.reference)
        if not text or " ".join(source.quote.split()) not in " ".join(text.split()):
            raise ValueError("Unverified evidence")
    if runtime.evidence and answer.kind != "clarification" and not answer.evidence:
        raise ValueError("Cite read evidence for source-based advice")
    return {
        "kind": answer.kind,
        "text": answer.text,
        "evidence": [x.model_dump() for x in answer.evidence],
    }
