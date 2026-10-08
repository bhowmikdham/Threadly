"""Only the two unused Calendar attempts may append to the ten-call ledger."""

import json

import pytest

from tests.test_chat_context_evaluation_budget import Raw
from tools.evaluate_chat_context import APPROVED_MODEL, COUNT_MODEL, BudgetExceeded
from tools.resume_chat_context7 import Resume7Budget


def seed(tmp_path):
    baseline = {
        "inference_model": APPROVED_MODEL,
        "count_model": COUNT_MODEL,
        "calls": [
            {
                "scenario": name,
                "outcome": "completed",
                "usage": {"inputTokens": 100, "outputTokens": 12},
            }
            for name in ["email"] * 6 + ["calendar"] * 4
        ],
        "preflights": [{"scenario": "email", "input_tokens": 100}],
        "blocked_operations": [],
    }
    ledger, segment = tmp_path / "third.json", tmp_path / "final.json"
    ledger.write_text(json.dumps(baseline))
    return ledger, segment, baseline


def test_final_two_preserve_all_ten_receipts_and_never_allow_email(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    budget = Resume7Budget(ledger, segment, baseline, pause_seconds=0)
    raw = Raw()
    try:
        budget.scenario = "email"
        with pytest.raises(BudgetExceeded):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        budget.scenario = "calendar"
        for _ in range(2):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        with pytest.raises(BudgetExceeded):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 2 and len(budget.calls) == 12
        assert budget.calls[:10] == baseline["calls"]
        assert budget.preflights[:1] == baseline["preflights"]
    finally:
        budget.close()


@pytest.mark.parametrize("stop", ["semantic", "provider", "preflight", "expired"])
def test_stop_and_deadline_survive_restart(tmp_path, stop):
    ledger, segment, baseline = seed(tmp_path)
    budget = Resume7Budget(ledger, segment, baseline, pause_seconds=0)
    budget.scenario = "calendar"
    raw = Raw()
    if stop == "semantic":
        budget.stop("semantic_regression")
    elif stop in {"provider", "preflight"}:

        def fail(**request):
            if stop == "provider":
                assert len(json.loads(ledger.read_text())["calls"]) == 11
            raise TimeoutError()

        setattr(raw, "converse" if stop == "provider" else "count_tokens", fail)
        with pytest.raises((TimeoutError, BudgetExceeded)):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    budget.close()
    if stop == "expired":
        saved = json.loads(segment.read_text())
        saved["started_unix"] -= 901
        saved["deadline_unix"] -= 901
        segment.write_text(json.dumps(saved))
    budget = Resume7Budget(ledger, segment, baseline, pause_seconds=0)
    budget.scenario = "calendar"
    try:
        raw = Raw()
        with pytest.raises(BudgetExceeded):
            budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 0
        assert len(budget.calls) == (11 if stop == "provider" else 10)
    finally:
        budget.close()


def test_changed_original_receipt_is_rejected(tmp_path):
    ledger, segment, baseline = seed(tmp_path)
    corrupted = json.loads(ledger.read_text())
    corrupted["calls"][9]["usage"]["inputTokens"] += 1
    ledger.write_text(json.dumps(corrupted))
    with pytest.raises(BudgetExceeded):
        Resume7Budget(ledger, segment, baseline, pause_seconds=0)
