"""Replay synthetic classification cases. Live mode is explicit and costs inference.

Example: python -m app.classification.evaluate --fixtures ../ml/evals/classification/v1.json
Default validates fixtures only, not model quality. --predictions reads saved model
JSON keyed by case ID. --live invokes the separately configured model on synthetic
cases only. No Gmail reads, database access, automatic retry or external writes.
"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from app.classification.contracts import Decision
from app.classification.provider import get_provider
from app.classification.service import POLICY_VERSION, PROMPT, SCHEMA, parse_decision, release


async def run(args):
    dataset = json.loads(args.fixtures.read_text())
    cases = dataset["cases"]
    if dataset["policy_version"] != POLICY_VERSION or not cases:
        raise ValueError("Wrong or empty fixture release")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs")
    for case in cases:
        source_ids = {m["source_id"] for m in case["input"]["messages"]}
        parse_decision(json.dumps(case["expected"]), source_ids)
    mode = "live" if args.live else "replay" if args.predictions else "fixture_validation"
    report = {"mode": mode, "policy_version": POLICY_VERSION, "cases": len(cases),
              "model_quality_verified": False}
    if mode == "fixture_validation":
        return report
    predictions = json.loads(args.predictions.read_text()) if args.predictions else {}
    if args.predictions and set(predictions) != set(ids):
        raise ValueError("Prediction IDs must match the fixture set exactly")
    pinned = release() if args.live else None
    scores = {field: {"correct": 0, "total": 0} for field in
              ("needs_reply", "priority", "category", "action")}
    invalid, joint, failures = 0, 0, []
    errors, latency = {}, {}
    for case in cases:
        started = perf_counter()
        try:
            if args.live:
                value = await get_provider().generate(
                    PROMPT, {**case["input"], "output_schema": SCHEMA},
                    model=pinned.model, region=pinned.region, timeout=pinned.timeout,
                    **({"flow": pinned.flow} if pinned.flow else {}),
                )
            else:
                saved = predictions[case["id"]]
                value = saved if isinstance(saved, str) else json.dumps(saved)
            # Keep malformed raw completions too, so a failed live run is replayable.
            predictions[case["id"]] = value
            parsed = parse_decision(value, {m["source_id"] for m in case["input"]["messages"]})
            predictions[case["id"]] = parsed.model_dump()
        except Exception as error:
            invalid += 1
            failures.append(case["id"])
            code = getattr(error, "code", "evaluation_failed")
            errors[case["id"]] = code
            predictions.setdefault(case["id"], {"evaluation_error": code})
            parsed = None
        latency[case["id"]] = round((perf_counter() - started) * 1000)
        expected = Decision.model_validate(case["expected"])
        joint += int(parsed is not None and parsed.status == expected.status
                     and parsed.labels == expected.labels)
        if expected.labels:
            for field, score in scores.items():
                score["total"] += 1
                score["correct"] += int(parsed is not None and parsed.labels is not None
                                        and getattr(parsed.labels, field)
                                        == getattr(expected.labels, field))
    report.update(invalid_outputs_or_provider_failures=invalid, failed_cases=failures,
                  joint_correct=joint, field_scores=scores, predictions=predictions,
                  release_id=pinned.identifier if pinned else None,
                  flow=pinned.flow.model_dump() if pinned and pinned.flow else None,
                  errors=errors, request_latency_ms=latency,
                  limitation="Synthetic seed cases only; not production acceptance or calibration.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--predictions", type=Path)
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=2))


if __name__ == "__main__":
    main()
