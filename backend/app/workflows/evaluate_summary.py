"""Replay synthetic summaries offline or with explicit billed runtime Flow invocation."""

import argparse
import asyncio
import json
from pathlib import Path

from app.assistant import summary, summary_policy, summary_quality
from app.model_client.structured import reject_duplicate_keys
from app.workflows.bedrock_flows import FlowError, FlowInvoker
from app.workflows.registry import FlowEntry, Manifest
from app.workflows.summary_checks import SummaryChecks


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


def validate_fixtures(fixtures: dict) -> list[dict]:
    if not isinstance(fixtures.get("version"), str) or not fixtures["version"].strip():
        raise ValueError("A fixture version is required")
    cases = fixtures["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        raise ValueError("Use 1-10 reviewed synthetic cases")
    ids = set()
    # Validate the entire suite, including its assertions, before any billed call.
    for case in cases:
        if (
            not isinstance(case.get("rubric"), list)
            or not case["rubric"]
            or not all(isinstance(item, str) and item.strip() for item in case["rubric"])
        ):
            raise ValueError("Every fixture needs a review rubric")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in ids:
            raise ValueError("Fixture IDs must be unique nonempty strings")
        ids.add(case["id"])
        if (
            not isinstance(case["bodies"], list)
            or not 1 <= len(case["bodies"]) <= 50
            or not all(isinstance(body, str) and body.strip() for body in case["bodies"])
            or sum(len(body) for body in case["bodies"]) > 12000
            or not isinstance(case["instruction"], str)
            or not case["instruction"].strip()
            or len(case["instruction"]) > 8000
        ):
            raise ValueError("Fixture exceeds the bounded context contract")
        summary_quality.make_artifact(json.dumps(case["expected"]), "fixture", snapshot(case))
        checks = SummaryChecks.model_validate(case.get("checks", {}))
        if checks.failures(case["expected"]):
            raise ValueError("Human reference contradicts fixture checks")
    return cases


def record_output(case: dict, text: str | None, *, error_code: str | None = None) -> dict:
    record = {
        "id": case["id"],
        "contract_passed": False,
        "regression_checks_passed": False,
        "human_reference": case["expected"],
        "rubric": case["rubric"],
        "checks": case.get("checks", {}),
        "quality_review": "pending",
    }
    if error_code:
        return {**record, "error_code": error_code}
    if text is None:
        return {**record, "error_code": "not_run"}
    record["observed_output"] = text
    try:
        artifact = summary_quality.make_artifact(text, "fixture", snapshot(case))
        failures = SummaryChecks.model_validate(case.get("checks", {})).failures(json.loads(text))
        record.update(
            contract_passed=True,
            artifact=artifact,
            regression_checks_passed=not failures,
            regression_failures=failures,
        )
        if failures:
            record.update(error_code="summary_regression_failed", quality_review="fail")
    except (ValueError, TypeError):
        record["error_code"] = "invalid_summary_output"
    return record


def report(fixtures: dict, records: list[dict], *, mode: str, candidate: str) -> dict:
    return {
        "schema_version": "1.1",
        "mode": mode,
        "candidate": candidate,
        "fixture_version": fixtures["version"],
        "fixture_hash": summary.digest(fixtures),
        # Contract used for checking; offline output does not prove its generating prompt.
        "contract_hash": summary_quality.contract_hash(),
        "generation_contract_hash": summary_quality.contract_hash() if mode == "invoke" else None,
        "cases": records,
        "total": len(records),
        "contract_passed": sum(c["contract_passed"] for c in records),
        "regression_checks_passed": sum(c["regression_checks_passed"] for c in records),
        "not_run": sum(c.get("error_code") == "not_run" for c in records),
        "language_quality_review": "pending",
        "production_approved": False,
    }


def evaluate_observations(fixtures: dict, observations: dict) -> dict:
    """Replay supplied synthetic outputs. Missing cases remain failures to complete the suite."""
    cases = validate_fixtures(fixtures)
    if (
        not isinstance(observations, dict)
        or set(observations) != {"schema_version", "candidate", "outputs"}
        or observations["schema_version"] != "1.0"
        or not isinstance(observations["candidate"], str)
        or not observations["candidate"].strip()
        or not isinstance(observations["outputs"], dict)
    ):
        raise ValueError("Invalid observations envelope")
    outputs = observations["outputs"]
    if set(outputs) - {case["id"] for case in cases}:
        raise ValueError("Unknown observation case ID")
    if any(not isinstance(value, (str, dict)) for value in outputs.values()):
        raise ValueError("An observed output must be a raw string or JSON object")
    records = []
    for case in cases:
        value = outputs.get(case["id"])
        text = json.dumps(value) if isinstance(value, dict) else value
        records.append(record_output(case, text))
    return report(fixtures, records, mode="offline", candidate=observations["candidate"])


async def evaluate(entry: FlowEntry, fixtures: dict, invoker=None) -> dict:
    cases = validate_fixtures(fixtures)
    invoker = invoker or FlowInvoker()
    records = []
    for case in cases:
        try:
            result = await invoker.invoke(
                entry, summary_quality.make_prompt(snapshot(case), case["instruction"])
            )
            record = record_output(case, result.text)
            record["provenance"] = result.provenance
        except FlowError as error:
            record = record_output(case, None, error_code=error.code)
        records.append(record)
    return report(fixtures, records, mode="invoke", candidate=summary_policy.VERSION)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(), object_pairs_hook=reject_duplicate_keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--invoke", action="store_true", help="Run up to ten billed synthetic calls")
    mode.add_argument(
        "--observations", type=Path, help="Replay saved synthetic outputs; no AWS calls"
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.invoke and args.manifest is None:
        parser.error("--invoke requires --manifest")
    if args.observations and args.manifest:
        parser.error("--manifest is only used with --invoke")
    fixtures = read_json(args.fixtures)
    validate_fixtures(fixtures)
    entry = None
    if args.invoke:
        manifest = Manifest.model_validate(read_json(args.manifest))
        entry = manifest.operations["summarise_thread"]
        if not isinstance(entry, FlowEntry):
            parser.error("Select an explicit published runtime summary Flow")
    observations = read_json(args.observations) if args.observations else None
    # Refuse to overwrite a previous report, before dispatching any model call.
    with args.output.open("x") as file:
        result = (
            evaluate_observations(fixtures, observations)
            if args.observations is not None
            else asyncio.run(evaluate(entry, fixtures))
        )
        file.write(json.dumps(result, indent=2) + "\n")
    if (
        result["contract_passed"] != result["total"]
        or result["regression_checks_passed"] != result["total"]
    ):
        raise SystemExit("Summary failures or missing cases: review the saved report")
    print(
        "Contract and regression checks passed. Human factual/relevance review is still required."
    )


if __name__ == "__main__":
    main()
