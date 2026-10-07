"""Offline guard checks only; no model invocation or credential discovery."""

import json
from types import SimpleNamespace

import pytest

from tests.test_conversation import configured  # noqa: F401
from tests.test_on_demand_gmail import setup  # noqa: F401
from tools.evaluate_chat_context import (
    APPROVED_MODEL,
    APPROVED_REGION,
    COUNT_MODEL,
    Budget,
    BudgetExceeded,
    install_dispatch_guard,
    scenarios,
)


class Raw:
    def __init__(self):
        self.calls = 0
        self.tokens = 10
        self.last = None
        self.last_count = None

    def count_tokens(self, **kwargs):
        self.last_count = kwargs
        return {"inputTokens": self.tokens}

    def converse(self, **kwargs):
        self.calls += 1
        self.last = kwargs
        return {"usage": {"inputTokens": self.tokens, "outputTokens": 12}, "stopReason": "tool_use"}


def test_live_harness_caps_every_scenario_and_total_before_inference():
    budget, raw = Budget(), Raw()
    client = budget.client(raw)
    for scenario in scenarios():
        budget.scenario = scenario["id"]
        for _ in range(6):
            client.converse(
                modelId=APPROVED_MODEL, messages=[], inferenceConfig={"maxTokens": 4000}
            )
        if len(budget.calls) < budget.max_calls:
            with pytest.raises(BudgetExceeded, match="scenario"):
                client.converse(modelId=APPROVED_MODEL, messages=[])
    assert raw.calls == 18
    assert raw.last["inferenceConfig"]["maxTokens"] == 1800
    assert raw.last["modelId"] == APPROVED_MODEL
    assert raw.last_count["modelId"] == COUNT_MODEL
    with pytest.raises(BudgetExceeded, match="18-call"):
        client.converse(modelId=APPROVED_MODEL, messages=[])
    assert raw.calls == 18


def test_live_harness_rejects_oversized_input_without_inference():
    budget, raw = Budget(), Raw()
    raw.tokens = 32001
    with pytest.raises(BudgetExceeded):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    assert raw.calls == 0
    assert budget.calls == []
    assert budget.halted
    assert len(scenarios()) == 3
    assert len(scenarios()[0]["seed"]) > 40


def test_preflight_failure_stops_without_paid_fallback(monkeypatch):
    budget, raw = Budget(), Raw()

    def unsupported(**request):
        raise RuntimeError("CountTokens does not support this profile")

    monkeypatch.setattr(raw, "count_tokens", unsupported)
    with pytest.raises(BudgetExceeded, match="no paid fallback"):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    assert budget.halted and raw.calls == 0


def test_failed_inference_consumes_budget_and_stops_retry(monkeypatch):
    budget, raw = Budget(), Raw()

    def fail(**request):
        raise TimeoutError()

    monkeypatch.setattr(raw, "converse", fail)
    with pytest.raises(TimeoutError):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    assert len(budget.calls) == 1
    assert budget.calls[0]["error_type"] == "TimeoutError"
    with pytest.raises(BudgetExceeded, match="stopped"):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])


@pytest.mark.parametrize(
    "extra",
    [
        {"modelId": "another-model"},
        {"guardrailConfig": {}},
        {"additionalModelRequestFields": {"thinking": {"type": "enabled"}}},
        {"system": [{"cachePoint": {"type": "default"}}]},
        {"serviceTier": {"type": "priority"}},
    ],
)
def test_unapproved_request_features_fail_before_dispatch(extra):
    raw = Raw()
    with pytest.raises(BudgetExceeded):
        Budget().client(raw).converse(**{"modelId": APPROVED_MODEL, "messages": [], **extra})
    assert raw.calls == 0


def test_deadline_blocks_new_inference():
    budget, raw = Budget(), Raw()
    budget.started -= budget.max_seconds + 1
    with pytest.raises(BudgetExceeded, match="time budget"):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    assert raw.calls == 0


def test_budget_ledger_reserves_before_dispatch_and_survives_restarts(tmp_path):
    path = tmp_path / "budget.json"
    budget, raw = Budget(path), Raw()
    budget.scenario = "long_arbitrary_correction"
    original = raw.converse

    def observe(**request):
        saved = json.loads(path.read_text())
        assert saved["calls"][-1]["outcome"] == "pending"
        return original(**request)

    raw.converse = observe
    for _ in range(6):
        budget.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
    with pytest.raises(BlockingIOError):
        Budget(path)
    budget.close()
    resumed = Budget(path)
    resumed.scenario = "long_arbitrary_correction"
    try:
        assert len(resumed.calls) == 6
        with pytest.raises(BudgetExceeded, match="scenario"):
            resumed.client(raw).converse(modelId=APPROVED_MODEL, messages=[])
        assert raw.calls == 6
    finally:
        resumed.close()


def test_global_dispatch_guard_covers_independent_default_clients(monkeypatch):
    from botocore.client import BaseClient

    dispatched = []

    def original(client, operation, request):
        dispatched.append(operation)
        if operation == "CountTokens":
            return {"inputTokens": 30}
        return {"usage": {"inputTokens": 30, "outputTokens": 12}}

    monkeypatch.setattr(BaseClient, "_make_api_call", original)
    budget = Budget()
    install_dispatch_guard(monkeypatch, budget)

    def client():
        return SimpleNamespace(
            meta=SimpleNamespace(
                service_model=SimpleNamespace(service_name="bedrock-runtime"),
                region_name=APPROVED_REGION,
                config=SimpleNamespace(retries={"total_max_attempts": 1}),
            )
        )

    for _ in range(3):  # Coordinator, auxiliary interpreter and worker each get a new client.
        BaseClient._make_api_call(client(), "Converse", {"modelId": APPROVED_MODEL})
    assert len(budget.calls) == 3
    assert dispatched == ["CountTokens", "Converse"] * 3
    for operation in ("CountTokens", "InvokeModel", "InvokeFlow"):
        with pytest.raises(BudgetExceeded):
            BaseClient._make_api_call(client(), operation, {})
    wrong = client()
    wrong.meta.region_name = "us-east-1"
    with pytest.raises(BudgetExceeded):
        BaseClient._make_api_call(wrong, "Converse", {"modelId": APPROVED_MODEL})
    retrying = client()
    retrying.meta.config.retries = {"total_max_attempts": 2}
    with pytest.raises(BudgetExceeded):
        BaseClient._make_api_call(retrying, "Converse", {"modelId": APPROVED_MODEL})
    assert len(dispatched) == 6


async def test_harness_report_rehearsal_uses_scripted_model_only(
    configured,  # noqa: F811
    db_sessionmaker,
    monkeypatch,
    tmp_path,  # noqa: F811
):
    from app.model_client import conversation
    from tests.test_conversation import Model, tool
    from tools.evaluate_chat_context import test_live_context_scenarios

    model = Model(*[tool("respond", kind="message", text="Scripted rehearsal.") for _ in range(10)])
    monkeypatch.setattr(conversation, "ConversationModel", lambda: model)
    monkeypatch.setenv("BEDROCK_MODEL_ID", APPROVED_MODEL)
    monkeypatch.setenv("BEDROCK_REGION", APPROVED_REGION)
    destination = tmp_path / "synthetic-report.json"
    monkeypatch.setenv("THREADLY_CONTEXT_EVAL_REPORT", str(destination))
    # Direct invocation bypasses only pytest's skip marker, not budget dispatch.
    # Every decision is scripted; no AWS runtime client is instantiated.
    await test_live_context_scenarios(configured, db_sessionmaker, monkeypatch)
    report = json.loads(destination.read_text())
    assert report["calls"] == []
    assert len(report["scenarios"]) == 3
    assert sum(len(s["turns"]) for s in report["scenarios"]) == 10
    assert all("goals" in turn for s in report["scenarios"] for turn in s["turns"])
