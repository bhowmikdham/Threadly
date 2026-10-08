"""Offline proof that a new approved time window never restores paid allowance."""

import json
from copy import deepcopy
from decimal import Decimal

import pytest

from tests.test_chat_context_evaluation_budget import Raw
from tools.evaluate_chat_context import APPROVED_MODEL, COUNT_MODEL, BudgetExceeded
from tools.resume_chat_context_evaluation import ResumeBudget, cost


def seed(tmp_path):
    baseline = {
        "inference_model": APPROVED_MODEL,
        "count_model": COUNT_MODEL,
        "calls": [
            {
                "scenario": "old",
                "outcome": "completed",
                "usage": {"inputTokens": 100, "outputTokens": 12},
            }
            for _ in range(9)
        ],
        "preflights": [{"scenario": "old", "input_tokens": 100}],
    }
    ledger, segment = tmp_path / "second.json", tmp_path / "resume.json"
    ledger.write_text(json.dumps(baseline))
    return ledger, segment, baseline


def test_remaining_nine_keep_original_receipts_and_cumulative_cap(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    budget, raw = ResumeBudget(ledger, segment, baseline, pause_seconds=0), Raw()
    try:
        for i in range(9):
            budget.scenario = "resume." + str(i // 3)
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        with pytest.raises(BudgetExceeded, match="allowance"):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert len(budget.calls) == 18 and raw.calls == 9
        assert budget.calls[:9] == baseline["calls"]
        assert cost(budget.calls) < Decimal("1")
    finally:
        budget.close()


def test_window_survives_restart_and_rejects_expiry(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    first = ResumeBudget(ledger, segment, baseline, pause_seconds=0)
    first.scenario = "resume.goal"
    first.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    metadata = json.loads(segment.read_text())
    metadata["started_unix"] -= 901
    metadata["deadline_unix"] -= 901
    segment.write_text(json.dumps(metadata))
    second, raw = ResumeBudget(ledger, segment, baseline, pause_seconds=0), Raw()
    try:
        assert len(second.calls) == 10
        with pytest.raises(BudgetExceeded, match="time budget"):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 0
    finally:
        second.close()


def test_failed_attempt_remains_counted_and_cannot_retry_after_restart(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    first, raw = ResumeBudget(ledger, segment, baseline, pause_seconds=0), Raw()
    first.scenario = "resume.goal"

    def fail(**request):
        assert len(json.loads(ledger.read_text())["calls"]) == 10
        raise TimeoutError()

    raw.converse = fail
    with pytest.raises(TimeoutError):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    second = ResumeBudget(ledger, segment, baseline, pause_seconds=0)
    try:
        assert len(second.calls) == 10
        with pytest.raises(BudgetExceeded, match="stopped"):
            second.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    finally:
        second.close()


def test_modified_prior_receipt_or_missing_window_is_rejected(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    altered = deepcopy(baseline)
    altered["calls"][0]["usage"]["inputTokens"] = 101
    ledger.write_text(json.dumps(altered))
    with pytest.raises(BudgetExceeded, match="receipts changed"):
        ResumeBudget(ledger, segment, baseline, pause_seconds=0)


def test_cost_reserves_failed_or_unknown_attempts():
    assert cost([{"outcome": "pending"}, {"outcome": "failed"}]) == Decimal("0.09922")
