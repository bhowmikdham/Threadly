"""Known semantic failures are evaluation failures, not generic runtime detection."""

import copy
import json
from pathlib import Path

import pytest

from app.assistant import summary_policy, summary_policy_v1, summary_policy_v1_0_1, summary_quality
from app.workflows.evaluate_summary import (
    evaluate,
    evaluate_observations,
    snapshot,
    validate_fixtures,
)
from app.workflows.registry import FlowEntry
from tests.test_workflow_runtime import TARGET

FIXTURES = json.loads((Path(__file__).parent / "fixtures/summary_quality_v2.json").read_text())
OBSERVATIONS = json.loads(
    (
        Path(__file__).parents[2] / "docs/evaluation/summary-console-observations-v1_0_1.json"
    ).read_text()
)


def observations_for(case, value):
    return {"schema_version": "1.0", "candidate": "synthetic-test", "outputs": {case["id"]: value}}


def test_user_reported_delivery_failure_passes_schema_but_fails_behavior_checks():
    report = evaluate_observations(FIXTURES, OBSERVATIONS)
    failure = next(c for c in report["cases"] if c["id"] == "quoted-source-injection")
    assert failure["contract_passed"]  # Do not pretend structural validation proves grounding.
    assert failure["error_code"] == "summary_regression_failed"
    assert "open_questions:expected_empty" in failure["regression_failures"]
    assert "overview:forbidden_phrase:downstream" in failure["regression_failures"]
    assert report["regression_checks_passed"] == 1
    assert report["not_run"] == 8 and report["total"] == 10
    assert report["generation_contract_hash"] is None
    assert not report["production_approved"]


def test_advice_hidden_only_in_overview_is_still_a_fixture_failure():
    case = next(c for c in FIXTURES["cases"] if c["id"] == "passive-delivery-update")
    value = {
        **case["expected"],
        "overview": "Delivery delayed until Friday. Assess downstream impacts.",
    }
    report = evaluate_observations({**FIXTURES, "cases": [case]}, observations_for(case, value))
    assert report["contract_passed"] == 1
    assert report["regression_checks_passed"] == 0


def test_emptying_every_array_is_not_a_valid_fix_for_explicit_work():
    case = next(c for c in FIXTURES["cases"] if c["id"] == "explicit-outstanding-work")
    value = {**case["expected"], "actions": []}
    report = evaluate_observations({**FIXTURES, "cases": [case]}, observations_for(case, value))
    assert report["cases"][0]["regression_failures"] == ["actions:expected_nonempty"]


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda c: c["id"])
def test_reviewed_references_meet_declared_constraints_without_claiming_model_success(case):
    report = evaluate_observations(
        {**FIXTURES, "cases": [case]}, observations_for(case, case["expected"])
    )
    assert report["contract_passed"] == report["regression_checks_passed"] == 1
    assert report["language_quality_review"] == "pending"
    assert not report["production_approved"]


def test_constraints_do_not_require_exact_reference_wording():
    case = next(c for c in FIXTURES["cases"] if c["id"] == "passive-delivery-update")
    value = {**case["expected"], "overview": "The delivery will arrive later, on Friday."}
    result = evaluate_observations({**FIXTURES, "cases": [case]}, observations_for(case, value))
    assert result["regression_checks_passed"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f["cases"].append(f["cases"][0]),
        lambda f: f["cases"][1].update(id=f["cases"][0]["id"]),
        lambda f: f["cases"][-1].update(checks={"empty_fields": ["invalid"]}),
        lambda f: f["cases"][-1].update(checks={"nonempty_fields": ["actions"]}),
        lambda f: f["cases"][-1].update(checks={"forbidden_overview_phrases": [""]}),
        lambda f: f["cases"][-1].pop("rubric"),
    ],
)
async def test_bad_fixture_or_constraints_stop_before_any_billed_call(mutate):
    fixtures = copy.deepcopy(FIXTURES)
    mutate(fixtures)

    class NoCalls:
        async def invoke(self, *_args):
            pytest.fail("No invocation may precede complete fixture validation")

    with pytest.raises(ValueError):
        await evaluate(FlowEntry.model_validate(TARGET), fixtures, NoCalls())


def test_unknown_observation_id_cannot_be_silently_skipped():
    with pytest.raises(ValueError, match="Unknown observation"):
        evaluate_observations(FIXTURES, observations_for({"id": "misspelled"}, {}))


def test_null_offline_file_cannot_select_live_invocation(tmp_path, monkeypatch):
    import sys

    from app.workflows import evaluate_summary

    fixtures = tmp_path / "fixtures.json"
    observations = tmp_path / "observations.json"
    fixtures.write_text(json.dumps(FIXTURES))
    observations.write_text("null")

    def no_invoke(*_args):
        pytest.fail("Offline mode must never fall through to live invocation")

    monkeypatch.setattr(evaluate_summary, "evaluate", no_invoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_summary",
            "--fixtures",
            str(fixtures),
            "--observations",
            str(observations),
            "--output",
            str(tmp_path / "report.json"),
        ],
    )
    with pytest.raises(ValueError, match="Invalid observations envelope"):
        evaluate_summary.main()


def test_all_three_policy_identities_remain_replayable_and_distinct():
    hashes = []
    for policy in (summary_policy_v1, summary_policy_v1_0_1, summary_policy):
        hash_value = summary_quality.contract_hash(policy)
        hashes.append(hash_value)
        release = {
            "workflow": summary_quality.RELEASE,
            "contract_hash": hash_value,
            "base_release": {},
        }
        assert summary_quality.policy_for_release(release) is policy
        prompt = summary_quality.make_prompt(
            snapshot(FIXTURES["cases"][0]), "Summarise", policy=policy
        )
        assert prompt.startswith(policy.PROMPT)
    assert hashes[:2] == [
        "4235198f0358cb28e5898be9814a32671e60c6f393b0beb1f011d074788519cc",
        "44dcaea33c8a516b72d982185c2f04c63de917d62361cf7f6d2e72d010686cdc",
    ]
    assert len(set(hashes)) == 3
    assert summary_policy.VERSION == "summary-quality-1.0.2"
    assert "clearly necessary next step" not in summary_policy.PROMPT
    assert "next step that matters" in summary_policy_v1_0_1.PROMPT
    validate_fixtures(FIXTURES)
