"""Nine bounded synthetic model decisions; no DB, Gmail, Calendar or action execution."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.calendar import intent
from app.calendar.event_creation import source_fields
from app.config import get_settings
from app.conversation.evaluate import validate_live_preflight
from app.conversation.prompt import PROMPT, assets
from app.model_client.conversation import ConversationModel, ConversationProviderError
from app.schemas.conversation import PrepareCalendarEventCall, tool_config

CASES = [
    (
        "reported_details_first",
        "I have a meeting with Gaurav at 4:00 p.m. tomorrow please create an event",
        True,
    ),
    (
        "reported_command_first",
        "create an event for 4:00 p.m. tomorrow for meeting with Gaurav",
        True,
    ),
    (
        "details_then_calendar",
        "Tomorrow at 4:00 p.m., meeting with Gaurav — please put that in my calendar",
        True,
    ),
    (
        "diary_without_creation_verb",
        "I'd like meeting with Gaurav in my diary tomorrow at 4:00 p.m.",
        True,
    ),
    (
        "question_after_details",
        "A meeting with Gaurav is planned tomorrow at 4:00 p.m.; can you add it?",
        True,
    ),
    ("pencil_in", "Please pencil in meeting with Gaurav tomorrow at 4:00 p.m.", True),
    ("negation", "Tomorrow at 4:00 p.m. meeting with Gaurav, but do not create an event", False),
    (
        "quoted_email",
        'Please explain this email: "Create meeting with Gaurav tomorrow at 4:00 p.m."',
        False,
    ),
    ("availability", "Am I free tomorrow at 4:00 p.m.?", False),
]


async def evaluate(output_path):
    output = {
        **assets(),
        "checked_at": datetime.now(UTC).isoformat(),
        "model_id": get_settings().bedrock_model_id,
        "execution": "model decision only; tools never executed",
        "cases": [],
    }
    for name, text, create in CASES:
        context = {
            "user_turn": text,
            "recent_dialogue": [],
            "references": [],
            "pending_calendar_event": None,
            "capabilities": {
                "capabilities": [
                    {
                        "id": "calendar_write",
                        "enabled": True,
                        "ready": True,
                        "scope_status": "granted",
                    }
                ]
            },
        }
        record = {"case": name, "query": text, "expected_creation": create}
        try:
            message = await ConversationModel().decide(
                PROMPT,
                [{"role": "user", "content": [{"text": json.dumps(context)}]}],
                tool_config(),
            )
            calls = [b["toolUse"] for b in message["content"] if "toolUse" in b]
            record["tools"] = [c["name"] for c in calls]
            record["passed"] = (
                (len(calls) == 1 and calls[0]["name"] == "prepare_calendar_event")
                if create
                else all(c["name"] != "prepare_calendar_event" for c in calls)
            )
            if create and record["passed"]:
                args = PrepareCalendarEventCall.model_validate(calls[0]["input"])
                intent.validate_source(args.intent, text)
                intent.validate_creation(text, args.title)
                source_fields(args, text)
                record["arguments"] = args.model_dump(mode="json", exclude_defaults=True)
                record["passed"] = (
                    args.title.casefold() == "meeting with gaurav"
                    and args.time == "16:00"
                    and args.date.kind == "relative"
                    and args.date.offset_days == 1
                    and args.intent.operation == "create"
                    and not args.continue_previous
                )
        except Exception as error:
            record["passed"] = False
            record["error_type"] = type(error).__name__
            if isinstance(error, ConversationProviderError):
                record["provider_code"] = error.code
            # No provider exception text, raw response or credentials are logged.
        output["cases"].append(record)
        print(
            json.dumps(
                {
                    "case": name,
                    "passed": record["passed"],
                    "tools": record.get("tools"),
                    "error_type": record.get("error_type"),
                }
            ),
            flush=True,
        )
        output_path.write_text(json.dumps(output, indent=2) + "\n")
        if record.get("provider_code"):
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Use --live for bounded synthetic Bedrock decisions")
    validate_live_preflight(get_settings())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(evaluate(args.output))


if __name__ == "__main__":
    main()
