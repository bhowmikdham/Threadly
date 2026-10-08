"""Newly approved 12-attempt allowance; offline fakes only."""

import json
from decimal import Decimal

import pytest

from tests.test_chat_context_evaluation_budget import Raw
from tools.evaluate_chat_context import APPROVED_MODEL, BudgetExceeded
from tools.evaluate_chat_context5 import Context5Budget
from tools.resume_chat_context_evaluation import cost


def test_case_and_combined_limits_survive_restart(tmp_path):
    ledger = tmp_path / "third.json"
    raw = Raw()
    first = Context5Budget(ledger, pause_seconds=0)
    first.scenario = "email"
    for _ in range(6):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    with pytest.raises(BudgetExceeded, match="case allowance"):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    second = Context5Budget(ledger, pause_seconds=0)
    second.scenario = "calendar"
    try:
        for _ in range(6):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        with pytest.raises(BudgetExceeded, match="Twelve-attempt"):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert len(second.calls) == raw.calls == 12
        assert cost(second.calls) < Decimal("0.60")
    finally:
        second.close()


def test_expired_window_does_not_reset_on_restart(tmp_path):
    ledger = tmp_path / "third.json"
    first = Context5Budget(ledger, pause_seconds=0)
    first.close()
    path = tmp_path / "third.json.identity.json"
    identity = json.loads(path.read_text())
    identity["started_unix"] -= 901
    identity["deadline_unix"] -= 901
    path.write_text(json.dumps(identity))
    second, raw = Context5Budget(ledger, pause_seconds=0), Raw()
    second.scenario = "email"
    try:
        with pytest.raises(BudgetExceeded, match="time budget"):
            second.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 0
    finally:
        second.close()


def test_failed_attempt_is_reserved_and_cannot_retry_after_restart(tmp_path):
    ledger = tmp_path / "third.json"
    first, raw = Context5Budget(ledger, pause_seconds=0), Raw()
    first.scenario = "calendar"

    def fail(**request):
        assert len(json.loads(ledger.read_text())["calls"]) == 1
        raise TimeoutError()

    raw.converse = fail
    with pytest.raises(TimeoutError):
        first.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    first.close()
    second = Context5Budget(ledger, pause_seconds=0)
    second.scenario = "email"
    try:
        assert cost(second.calls) == Decimal("0.04961")
        with pytest.raises(BudgetExceeded, match="stopped"):
            second.client(Raw()).converse(modelId=APPROVED_MODEL, messages=[])
    finally:
        second.close()


def test_unapproved_case_and_missing_ledger_fail_closed(tmp_path):
    ledger = tmp_path / "third.json"
    budget, raw = Context5Budget(ledger, pause_seconds=0), Raw()
    budget.scenario = "extra"
    with pytest.raises(BudgetExceeded, match="Unapproved"):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    assert raw.calls == 0
    budget.close()
    ledger.unlink()
    with pytest.raises(BudgetExceeded, match="cannot reset"):
        Context5Budget(ledger, pause_seconds=0)


def test_tax_inclusive_reservations_are_within_both_caps():
    assert cost([{"outcome": "pending"}] * 6) == Decimal("0.29766")
    assert cost([{"outcome": "pending"}] * 12) == Decimal("0.59532")
