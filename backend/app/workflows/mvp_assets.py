"""Export/check versioned MVP prompts and schemas without accessing any provider."""

import argparse
import json
from pathlib import Path

from app.assistant import coordinator, meeting_responses, planning
from app.assistant.summary import digest
from app.schemas.coordinator import CoordinatedCommand
from app.schemas.meeting_response import ExtractedChoice
from app.workflows import auxiliary


def assets():
    operations = {
        "route_command": (coordinator.PROMPT, CoordinatedCommand, coordinator.RELEASE),
        "plan_actions": (planning.PROMPT, planning.GeneratedPlan, planning.RELEASE),
        "interpret_meeting_response": (
            meeting_responses.PROMPT,
            ExtractedChoice,
            meeting_responses.RELEASE,
        ),
    }
    return {
        "schema_version": "1.0",
        "operations": {
            op: {
                "release": version,
                "prompt": prompt,
                "output_schema": schema.model_json_schema(),
                "contract_hash": digest({"prompt": prompt, "schema": schema.model_json_schema()}),
            }
            for op, (prompt, schema, version) in operations.items()
        },
        "default_registry": auxiliary.Manifest(
            schema_version="1.0", operations={op: {"implementation": "native"} for op in operations}
        ).model_dump(),
        "live_quality_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    serialized = json.dumps(assets(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if args.output.read_text() != serialized:
            raise SystemExit("MVP prompt assets differ from runtime; regenerate and evaluate.")
        print("MVP prompt assets match runtime; no provider invocation.")
    else:
        args.output.write_text(serialized)


if __name__ == "__main__":
    main()
