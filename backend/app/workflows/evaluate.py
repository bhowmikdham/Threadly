"""Explicit, billed synthetic smoke test. No Gmail or Calendar access.

python -m app.workflows.evaluate --manifest candidate-registry.json --output report.json --invoke
This validates output contracts, not factual quality or production readiness.
"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.assistant import drafting, routing, summary
from app.workflows.bedrock_flows import FlowError, FlowInvoker
from app.workflows.registry import OPERATIONS, FlowEntry, Manifest

SNAPSHOT = {
    "omitted_messages": 0,
    "truncated_messages": 0,
    "messages": [
        {
            "message_id": "synthetic-message",
            "from_addr": "person@example.invalid",
            "sent_at": "2026-09-15T09:00:00+10:00",
            "body": "Please review the project proposal. The deadline is undecided.",
        }
    ],
}


def fixture(operation):
    instruction = {
        "summarise_thread": "Summarise this thread; retain uncertainty about deadlines.",
        "draft_reply": "Draft a reply saying I will review the proposal.",
        "draft_new": "Draft a short email asking a collaborator to review a proposal.",
    }[operation]
    mode = "reply" if operation == "draft_reply" else "new"
    envelope = {
        "to": ["person@example.invalid"],
        "cc": [],
        "bcc": [],
        "reply_message_id": "synthetic-message" if mode == "reply" else None,
        "reply": {
            "subject": "Re: Project",
            "gmail_thread_id": "synthetic-thread",
            "rfc_message_id": "<synthetic@example.invalid>",
        }
        if mode == "reply"
        else None,
    }
    claim = SimpleNamespace(
        task_id="synthetic-task",
        context_id="synthetic-context",
        instruction=instruction,
        snapshot=SNAPSHOT,
        draft_input=envelope,
    )
    prompt = (
        routing.summary_prompt(SNAPSHOT, instruction)
        if operation == "summarise_thread"
        else drafting.make_prompt(instruction, SNAPSHOT, envelope, mode)
    )
    return claim, mode, prompt


async def evaluate(manifest: Manifest, invoker=None):
    invoker = invoker or FlowInvoker()
    cases = []
    for operation in OPERATIONS:
        entry = manifest.operations[operation]
        if not isinstance(entry, FlowEntry):
            raise ValueError("Smoke evaluation requires three explicit Flow targets")
        claim, mode, prompt = fixture(operation)
        record = {
            "operation": operation,
            "fixture_version": "synthetic-v1",
            "definition_hash": entry.definition_hash,
            "passed": False,
        }
        try:
            result = await invoker.invoke(entry, prompt)
            artifact = (
                summary.make_artifact(result.text, claim.context_id, claim.snapshot)
                if operation == "summarise_thread"
                else drafting.make_artifact(result.text, claim, mode)
            )
            record.update(passed=True, provenance=result.provenance, artifact=artifact)
        except FlowError as error:
            record["error_code"] = error.code
        except (ValueError, TypeError):
            record["error_code"] = "invalid_generated_artifact"
        cases.append(record)
    return {
        "schema_version": "1.0",
        "cases": cases,
        "passed": sum(case["passed"] for case in cases),
        "total": len(cases),
        "language_quality_review": "pending",
        "production_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--invoke",
        action="store_true",
        required=True,
        help="Explicitly run three billed synthetic Flow invocations",
    )
    args = parser.parse_args()
    manifest = Manifest.model_validate(json.loads(args.manifest.read_text()))
    # Refuse overwriting an evaluation; report contains synthetic output only.
    with args.output.open("x") as output:
        result = asyncio.run(evaluate(manifest))
        output.write(json.dumps(result, indent=2) + "\n")
    if result["passed"] != result["total"]:
        raise SystemExit("One or more Flow smoke cases failed; inspect the report.")
    print("Three contract smoke cases passed. Language-quality review and rollout remain pending.")


if __name__ == "__main__":
    main()
