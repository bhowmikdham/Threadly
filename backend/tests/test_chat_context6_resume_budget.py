"""Offline proof that .6 resumes time, never replenishes the third allowance."""

import json
from decimal import Decimal

import pytest

from tests.test_chat_context_evaluation_budget import Raw
from tools.evaluate_chat_context import APPROVED_MODEL, COUNT_MODEL, BudgetExceeded
from tools.resume_chat_context6 import Resume6Budget
from tools.resume_chat_context_evaluation import cost


def seed(tmp_path):
    baseline = {
        "inference_model": APPROVED_MODEL,
        "count_model": COUNT_MODEL,
        "calls": [
            {
                "scenario": "email",
                "outcome": "completed",
                "usage": {"inputTokens": 100, "outputTokens": 12},
            }
            for _ in range(3)
        ],
        "preflights": [{"scenario": "email", "input_tokens": 100}],
        "blocked_operations": [],
    }
    ledger, segment = tmp_path / "third.json", tmp_path / "segment.json"
    ledger.write_text(json.dumps(baseline))
    return ledger, segment, baseline


def test_three_email_six_calendar_preserve_prefix_and_total_across_restart(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    raw, first = Raw(), Resume6Budget(ledger, segment, baseline, pause_seconds=0)
    first.scenario = "email"
    for _ in range(3):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    with pytest.raises(BudgetExceeded, match="case allowance"):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    second = Resume6Budget(ledger, segment, baseline, pause_seconds=0)
    second.scenario = "calendar"
    try:
        for _ in range(6):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        with pytest.raises(BudgetExceeded, match="allowance"):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 9 and len(second.calls) == 12
        assert second.calls[:3] == baseline["calls"]
        assert second.preflights[:1] == baseline["preflights"]
    finally:
        second.close()


@pytest.mark.parametrize("failure", ["semantic", "provider", "expired", "preflight"])
def test_persisted_stops_and_deadline_cannot_reset_on_restart(tmp_path, failure):
    ledger, segment, baseline = seed(tmp_path)
    first, raw = Resume6Budget(ledger, segment, baseline, pause_seconds=0), Raw()
    first.scenario = "email"
    if failure == "semantic":
        first.stop("semantic_regression")
    elif failure in {"provider", "preflight"}:

        def fail(**request):
            if failure == "provider":
                assert len(json.loads(ledger.read_text())["calls"]) == 4
            raise TimeoutError()

        if failure == "provider":
            raw.converse = fail
        else:
            raw.count_tokens = fail
        with pytest.raises((TimeoutError, BudgetExceeded)):
            first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    if failure == "expired":
        saved = json.loads(segment.read_text())
        saved["started_unix"] -= 901
        saved["deadline_unix"] -= 901
        segment.write_text(json.dumps(saved))
    second, raw = Resume6Budget(ledger, segment, baseline, pause_seconds=0), Raw()
    second.scenario = "calendar"
    try:
        with pytest.raises(BudgetExceeded, match="stopped|time budget"):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 0
        assert len(second.calls) == (4 if failure == "provider" else 3)
    finally:
        second.close()


@pytest.mark.parametrize("target", ["receipt", "preflight", "missing_ledger", "missing_window"])
def test_changed_original_evidence_or_missing_used_window_fails_closed(tmp_path, target):
    ledger, segment, baseline = seed(tmp_path)
    first = Resume6Budget(ledger, segment, baseline, pause_seconds=0)
    first.scenario = "email"
    first.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    changed = json.loads(ledger.read_text())
    if target == "receipt":
        changed["calls"][0]["usage"]["inputTokens"] += 1
    elif target == "preflight":
        changed["preflights"][0]["input_tokens"] += 1
    elif target == "missing_window":
        segment.unlink()
    ledger.write_text(json.dumps(changed))
    if target == "missing_ledger":
        ledger.unlink()
    with pytest.raises(BudgetExceeded):
        Resume6Budget(ledger, segment, baseline, pause_seconds=0)


@pytest.mark.parametrize("scope", ["case", "combined"])
def test_cost_reservation_blocks_before_dispatch(tmp_path, monkeypatch, scope):
    from tools import resume_chat_context6

    ledger, segment, baseline = seed(tmp_path)
    budget, raw = Resume6Budget(ledger, segment, baseline, pause_seconds=0), Raw()
    if scope == "combined":
        budget.scenario = "calendar"
        budget.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    budget.scenario = "email"
    try:
        assert cost([{"outcome": "pending"}] * 6) == Decimal("0.29766")
        monkeypatch.setattr(
            resume_chat_context6,
            "cost",
            lambda calls: (
                Decimal("0.27")
                if scope == "case"
                else Decimal("0.59" if len(calls) > 3 else "0.10")
            ),
        )
        with pytest.raises(BudgetExceeded, match="GST-inclusive cost cap"):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 0
    finally:
        budget.close()
