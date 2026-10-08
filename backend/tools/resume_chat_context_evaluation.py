"""Approved .4 segment of the existing second budget; never a fresh allocation."""
# ruff: noqa: E402, F401, F811, I001

import gzip
import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tests.conftest import db_engine, db_sessionmaker
from tests.test_conversation import configured
from tests.test_on_demand_gmail import setup
from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import budget as context_budget, store
from app.conversation.prompt import PROMPT, assets
from app.conversation.runtime import Runtime
from app.db.models import Conversation, ConversationGoal
from app.schemas.conversation import tool_config
from app.pii.masking import mask_structure
from tools.evaluate_chat_context import (
    APPROVED_MODEL,
    APPROVED_REGION,
    COUNT_MODEL,
    Budget,
    BudgetExceeded,
    install_dispatch_guard,
    prepare_runtime_clients,
)
from tools.evaluate_chat_context_followup import (
    install_calendar_fake,
    prepare_calendar_account,
    record_state,
    scenarios,
    seed_cases,
    step,
)
from tests.test_calendar_creation import turn

APPROVAL = "approved-resume-nine-second-budget-context-4"
MESSAGE = "Sentinel_06ff4ac627b48191bc8af71e1651a557"
RELEASE = "contextual-conversation-1.8.9+chat-context.4"
BASE_HASH = "63efbe33d018c1cdaa9f3c9c6b79d417e67a2197954a5e0ef08c56e62b9d6393"
SEGMENT = "second-budget-resume-context-4-20261007T132428Z"
PER_ATTEMPT = Decimal("0.04961")


def cost(calls):
    total = Decimal(0)
    for call in calls:
        usage = call.get("usage")
        if call.get("outcome") != "completed" or not usage:
            total += PER_ATTEMPT
        else:
            total += (
                (
                    Decimal(usage["inputTokens"]) * Decimal("1.1")
                    + Decimal(usage["outputTokens"]) * Decimal("5.5")
                )
                / Decimal(1_000_000)
                * Decimal("1.1")
            )
    return total


class ResumeBudget(Budget):
    """Keep all prior calls; a separately authorized segment supplies only time."""

    def __init__(self, ledger, segment_path, baseline, *, pause_seconds=8):
        super().__init__(ledger)
        self.baseline = baseline
        self.segment_path = Path(segment_path)
        self.pause_seconds = pause_seconds
        self.dispatch_lock = threading.Lock()
        try:
            self.validate_prefix()
            expected = {
                "segment": SEGMENT,
                "approval_message": MESSAGE,
                "approved_at": "2026-10-07T13:24:28Z",
                "release": RELEASE,
                "assets": assets(),
                "prior_attempts": 9,
                "prior_cost_including_gst": "0.30159129",
                "original_ledger_sha256": BASE_HASH,
                "maximum_cumulative_attempts": 18,
                "maximum_cumulative_cost_including_gst": "1.00",
                "planned_new_calls": {"goals": 4, "saved_artifact": 2, "calendar": 3},
            }
            if self.segment_path.exists():
                saved = json.loads(self.segment_path.read_text())
                if any(saved.get(key) != value for key, value in expected.items()):
                    raise BudgetExceeded("Resumption identity changed")
            else:
                if len(self.calls) != 9:
                    raise BudgetExceeded("Cannot start a new window after resumed calls")
                saved = {**expected, "started_unix": time.time()}
                saved["deadline_unix"] = saved["started_unix"] + 900
                with self.segment_path.open("x") as stream:
                    json.dump(saved, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            if saved["deadline_unix"] != saved["started_unix"] + 900:
                raise BudgetExceeded("Invalid persisted deadline")
            self.started -= max(0, time.time() - saved["started_unix"])
            self.segment = saved
            self.halted = any(c["outcome"] != "completed" for c in self.calls[9:])
        except Exception:
            self.close()
            raise

    def validate_prefix(self):
        if not 9 <= len(self.calls) <= 18 or len(self.baseline["calls"]) != 9:
            raise BudgetExceeded("Original nine attempts or cumulative cap changed")
        if self.calls[:9] != self.baseline["calls"]:
            raise BudgetExceeded("Original call receipts changed")
        n = len(self.baseline["preflights"])
        if self.preflights[:n] != self.baseline["preflights"]:
            raise BudgetExceeded("Original preflights changed")

    def save(self):
        self.validate_prefix()
        super().save()

    def client(self, raw):
        guarded = super().client(raw)
        budget = self

        class Timed:
            def converse(self, **request):
                with budget.dispatch_lock:
                    if cost(budget.calls) + PER_ATTEMPT > Decimal("1.00"):
                        raise BudgetExceeded("Cumulative tax-inclusive cost cap")
                    if budget.halted or len(budget.calls) >= 18:
                        raise BudgetExceeded("Existing second-budget allowance exhausted/stopped")
                    if budget.calls[9:] and budget.pause_seconds:
                        time.sleep(budget.pause_seconds)
                    before = len(budget.calls)
                    started = datetime.now(UTC).isoformat()
                    try:
                        return guarded.converse(**request)
                    finally:
                        if len(budget.calls) > before:
                            budget.calls[-1].update(
                                segment=SEGMENT,
                                release=RELEASE,
                                started_at=started,
                                finished_at=datetime.now(UTC).isoformat(),
                            )
                            budget.save()

        return Timed()


if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
    path = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    if len(json.loads(path.read_text())["calls"]) != 9:
        raise RuntimeError("Do not reseed a used resumption; preserve its DB and report")


@pytest.mark.skipif(os.getenv("THREADLY_CONTEXT_EVAL") != APPROVAL, reason="No live default")
async def test_approved_resumption(configured, db_sessionmaker, monkeypatch):
    from botocore.client import BaseClient
    from sqlalchemy.engine import make_url
    from app.calendar import permissions, service as calendar_service
    from app.model_client import conversation

    assert assets()["release"] == RELEASE
    url = make_url(os.environ["THREADLY_TEST_DB"])
    assert (url.host, url.port, url.database) == (
        "127.0.0.1",
        55439,
        "threadly_context_eval2_resume_test",
    )
    assert os.environ["THREADLY_REQUIRE_TEST_DB"] == "1"
    assert os.environ["BEDROCK_MODEL_ID"] == APPROVED_MODEL
    assert os.environ["BEDROCK_REGION"] == APPROVED_REGION
    ledger = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    assert ledger.name == "chat-context-second-approved-budget-ledger.json"
    directory = ledger.parent
    destination = directory / "chat-context-second-resumed-model-report.json"
    segment = directory / "chat-context-second-approved-budget-ledger.resume-context-4.json"
    original = ledger.read_bytes()
    assert hashlib.sha256(original).hexdigest() == BASE_HASH
    baseline = json.loads(original)
    assert cost(baseline["calls"]) == Decimal("0.30159129")
    assert not destination.exists() and not segment.exists()
    pricing = json.loads((directory / "chat-context-resume-pricing.json").read_text())
    assert pricing["rates"]["input"]["price"] == "1.1000000000"
    assert pricing["rates"]["output"]["price"] == "5.5000000000"
    assert (
        datetime.now(UTC) - datetime.fromisoformat(pricing["checked_at"])
    ).total_seconds() < 1800
    for key, value in {
        "BEDROCK_SMALL_MODEL_ID": APPROVED_MODEL,
        "BEDROCK_READ_TIMEOUT_S": "45",
        "INFERENCE_PROVIDER": "bedrock",
        "BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED": "true",
        "CALENDAR_WRITES_ENABLED": "true",
        "WRITE_PILOT_USER_IDS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(permissions, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(calendar_service, "get_session_factory", lambda: db_sessionmaker)
    await prepare_calendar_account(db_sessionmaker)
    calendar_calls = []
    install_calendar_fake(monkeypatch, calendar_calls)

    async def no_http(*args, **kwargs):
        raise AssertionError("Non-mocked HTTP disabled")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_http)
    with monkeypatch.context() as seed_guard:

        def no_aws(*args, **kwargs):
            raise AssertionError("AWS prohibited during scripted setup")

        seed_guard.setattr(BaseClient, "_make_api_call", no_aws)
        seeds = await seed_cases(db_sessionmaker)

    # Authenticate and count the full first request before the clock starts.
    prepare_runtime_clients(monkeypatch)
    previous = seeds[0]["previous"]
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, previous["conversation_id"]))
    request = turn(
        scenarios()[0]["turns"][0],
        conversation_id=previous["conversation_id"],
        expected_version=previous["version"],
    )
    context = await Runtime(1, request, state, db_sessionmaker).context()
    messages = [{"role": "user", "content": [{"text": json.dumps(context_budget.fit(context))}]}]
    masked, _ = mask_structure({"system": PROMPT, "messages": messages})
    raw = conversation._runtime_client()
    try:
        counted = raw.count_tokens(
            modelId=COUNT_MODEL,
            input={
                "converse": {
                    "system": [{"text": masked["system"]}],
                    "messages": masked["messages"],
                    "toolConfig": {**tool_config(), "toolChoice": {"any": {}}},
                }
            },
        )
    finally:
        raw.close()
    assert type(counted["inputTokens"]) is int and 0 <= counted["inputTokens"] <= 32000
    preflight = {
        **assets(),
        "at": datetime.now(UTC).isoformat(),
        "input_tokens": counted["inputTokens"],
        "request_id": counted["ResponseMetadata"]["RequestId"],
        "paid_calls": 0,
    }
    (directory / "chat-context-resume-full-preflight.json").write_text(
        json.dumps(preflight, indent=2)
    )
    budget = ResumeBudget(ledger, segment, baseline)
    install_dispatch_guard(monkeypatch, budget)
    report = {
        **assets(),
        "segment": budget.segment,
        "calls": budget.calls,
        "scenarios": [],
        "calendar_calls": calendar_calls,
        "human_review_required": True,
    }
    try:
        # Priority order: goal identity, saved artifact, then Calendar.
        for index, planned in ((0, 4), (2, 2), (1, 3)):
            case, seed = scenarios()[index], seeds[index]
            if budget.halted or len(budget.calls) >= 18:
                break
            budget.scenario = "resume4." + case["id"]
            # Goals may need a selection repair; prioritize them over Calendar coverage.
            budget.max_scenario_calls = 6 if index == 0 else planned
            entry = {**case, "planned_calls": planned, "scripted_setup": seed, "actual_turns": []}
            report["scenarios"].append(entry)
            previous = seed["previous"]
            alex_hash = None
            for text in case["turns"]:
                if budget.halted or len(budget.calls) >= 18:
                    entry["stopped"] = "cumulative_limit_or_provider_failure"
                    break
                item = {"user": text}
                entry["actual_turns"].append(item)
                try:
                    previous = await step(
                        db_sessionmaker, previous, text, conversation.ConversationModel()
                    )
                    item["response"] = previous
                    item.update(await record_state(db_sessionmaker, previous))
                    if index == 0 and text == case["turns"][1]:
                        alex_id = item["state"]["email_draft_goal"]["goal_id"]
                        async with db_sessionmaker() as db:
                            alex_hash = (
                                await db.get(
                                    ConversationGoal, (previous["conversation_id"], alex_id)
                                )
                            ).payload_hash
                    if index == 0 and text == case["turns"][2] and alex_hash:
                        async with db_sessionmaker() as db:
                            current = await db.get(
                                ConversationGoal, (previous["conversation_id"], alex_id)
                            )
                        item["alex_payload_unchanged"] = current.payload_hash == alex_hash
                        if not item["alex_payload_unchanged"]:
                            budget.halted = True
                            entry["stopped"] = "goal_identity_invariant_failure"
                except ApiError as exc:
                    item["error"] = {"code": exc.code, "status": exc.status}
                    break
                finally:
                    destination.write_text(json.dumps(report, indent=2, default=str) + "\n")
    finally:
        report.update(
            finished_at=datetime.now(UTC).isoformat(),
            cumulative_cost_including_gst=str(cost(budget.calls)),
            cumulative_attempts=len(budget.calls),
            remaining_attempts=18 - len(budget.calls),
        )
        destination.write_text(json.dumps(report, indent=2, default=str) + "\n")
        budget.validate_prefix()
        budget.close()
        get_settings.cache_clear()
