"""Offline replay of versioned extraction outputs; never a live model accuracy test."""

import argparse
import json
from pathlib import Path

from app.planner import scheduling_extraction as extraction
from app.schemas.scheduling_proposal import SchedulingProposalRequest


def evaluate(path):
    corpus = json.loads(path.read_text())
    if (
        corpus["release"] != extraction.RELEASE
        or corpus["contract_hash"] != extraction.contract_hash()
    ):
        raise ValueError("Fixture release does not match the installed extractor")
    binding = {
        "anchor_at": "2026-09-17T02:00:00+00:00",
        "anchor_source": "request_received",
        "preferences": {"timezone": "UTC", "default_duration_minutes": 30},
    }
    results = []
    for case in corpus["cases"]:
        request = SchedulingProposalRequest(
            schema_version="1.0",
            request_id=case["id"],
            instruction=case["instruction"],
            expected_preferences_version=1,
        )
        parsed = extraction.parse(json.dumps(case["output"]), request.instruction)
        state, result = extraction.compile_proposal(request, parsed, case["id"], binding)
        passed = state == case["state"]
        if state == "proposed":
            passed &= all(
                result["compiled_request"]["constraints"][key] == value
                for key, value in case["constraints"].items()
            )
        else:
            passed &= result["compiled_request"] is None
        results.append({"id": case["id"], "passed": passed, "state": state})
    return {
        "release": extraction.RELEASE,
        "contract_hash": extraction.contract_hash(),
        "evaluation": "synthetic_output_contract_replay",
        "live_model_evaluated": False,
        "case_count": len(results),
        "passed": sum(r["passed"] for r in results),
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", type=Path, default=Path("tests/fixtures/scheduling_extraction_v1.json")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.fixtures)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result["passed"] == result["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
