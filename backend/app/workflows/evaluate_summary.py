"""Explicit billed summary replay; schema checks never substitute for human quality review."""

import argparse
import asyncio
import json
from pathlib import Path

from app.assistant import summary, summary_quality
from app.workflows.bedrock_flows import FlowError, FlowInvoker
from app.workflows.registry import FlowEntry, Manifest


def snapshot(case: dict) -> dict:
    return {
        "omitted_messages": 0,
        "truncated_messages": 0,
        "messages": [
            {
                "message_id": f"fixture-{i}",
                "from_addr": "sender@example.invalid",
                "sent_at": None,
                "body": body,
            }
            for i, body in enumerate(case["bodies"], 1)
        ],
    }


async def evaluate(entry: FlowEntry, fixtures: dict, invoker=None) -> dict:
    cases = fixtures["cases"]
    if not 1 <= len(cases) <= 10:
        raise ValueError("Use 1-10 reviewed synthetic cases")
    # Validate every input before the first billed call.
    for case in cases:
        if (
            not 1 <= len(case["bodies"]) <= 50
            or not all(isinstance(body, str) for body in case["bodies"])
            or sum(len(body) for body in case["bodies"]) > 12000
            or len(case["instruction"]) > 8000
        ):
            raise ValueError("Fixture exceeds the bounded context contract")
        summary_quality.make_artifact(json.dumps(case["expected"]), "fixture", snapshot(case))
    invoker = invoker or FlowInvoker()
    records = []
    for case in cases:
        record = {
            "id": case["id"],
            "contract_passed": False,
            "human_reference": case["expected"],
            "rubric": case["rubric"],
            "quality_review": "pending",
        }
        context = snapshot(case)
        try:
            result = await invoker.invoke(
                entry, summary_quality.make_prompt(context, case["instruction"])
            )
            # Explicit synthetic evaluation report, never an application artifact/log.
            record["observed_output"] = result.text
            record["provenance"] = result.provenance
            artifact = summary_quality.make_artifact(result.text, "fixture", context)
            record.update(contract_passed=True, artifact=artifact, provenance=result.provenance)
        except FlowError as error:
            record["error_code"] = error.code
        except (ValueError, TypeError):
            record["error_code"] = "invalid_summary_output"
        records.append(record)
    return {
        "schema_version": "1.0",
        "fixture_version": fixtures["version"],
        "fixture_hash": summary.digest(fixtures),
        "contract_hash": summary_quality.contract_hash(),
        "cases": records,
        "total": len(records),
        "contract_passed": sum(c["contract_passed"] for c in records),
        "language_quality_review": "pending",
        "production_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--invoke",
        action="store_true",
        required=True,
        help="Explicitly run up to ten billed synthetic summary calls",
    )
    args = parser.parse_args()
    manifest = Manifest.model_validate(json.loads(args.manifest.read_text()))
    entry = manifest.operations["summarise_thread"]
    if not isinstance(entry, FlowEntry):
        raise SystemExit("Select an explicit published runtime summary Flow")
    fixtures = json.loads(args.fixtures.read_text())
    with args.output.open("x") as file:
        result = asyncio.run(evaluate(entry, fixtures))
        file.write(json.dumps(result, indent=2) + "\n")
    if result["contract_passed"] != result["total"]:
        raise SystemExit("Summary contract failures: review the saved report")
    print("Contract checks passed. Human factual/relevance review is still required.")


if __name__ == "__main__":
    main()
