"""Explicitly approved .7 segment: two remaining calls on the THIRD ledger."""
# ruff: noqa: E402, F401, F811, I001

import asyncio
import hashlib
import json
import os
import subprocess
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

# Pin the disposable DB/test settings before importing application modules.
from tests.conftest import db_engine, db_sessionmaker

from app.config import get_settings
from app.conversation import budget as context_budget
from app.conversation import store
from app.conversation.prompt import PROMPT, assets
from app.conversation.runtime import Runtime
from app.db.models import Conversation
from app.pii.masking import mask_structure
from app.schemas.conversation import tool_config
from tests.test_calendar_creation import turn
from tests.test_conversation import Model, configured, tool
from tests.test_on_demand_gmail import setup
from tools.evaluate_chat_context import (
    APPROVED_MODEL,
    APPROVED_REGION,
    COUNT_MODEL,
    Budget,
    BudgetExceeded,
    install_dispatch_guard,
    prepare_runtime_clients,
)
from tools.evaluate_chat_context5 import PRIOR_HASHES, write
from tools.evaluate_chat_context_followup import (
    install_calendar_fake,
    prepare_calendar_account,
    record_state,
    step,
)
from tools.resume_chat_context_evaluation import PER_ATTEMPT, cost

APPROVAL = "approved-final-two-third-budget-context7"
MESSAGE = "Sentinel_271c8e9d315c8191acf72c452c6b2983"
SEGMENT = "third-budget-context7-20261008T024600Z"
RELEASE = "contextual-conversation-1.8.9+chat-context.7"
RUNTIME = "30612dbd8c890ae0fc56944312af6556eaba7152"
EVIDENCE = "53a275917acbbf41bd696e0d42fedb3ed708067b"
BASE_HASH = "e253f8ab8f47324aa17f0ba2a7e353dc4789fe2ab5b32bfe54f95297c3fb8a0a"


class Resume7Budget(Budget):
    max_calls = 12

    def __init__(self, ledger, segment_path, baseline, *, pause_seconds=8):
        super().__init__(ledger)
        self.baseline, self.segment_path = deepcopy(baseline), Path(segment_path)
        self.dispatch_lock, self.pause_seconds = threading.Lock(), pause_seconds
        try:
            self.validate_prefix()
            expected = {
                "segment": SEGMENT,
                "approval_message": MESSAGE,
                "approved_at": "2026-10-08T02:46:00Z",
                "release": RELEASE,
                "assets": assets(),
                "runtime": RUNTIME,
                "evidence": EVIDENCE,
                "prior_attempts": 10,
                "prior_usd_including_gst": "0.34752047",
                "original_ledger_sha256": BASE_HASH,
                "maximum_cumulative_attempts": 12,
                "maximum_cumulative_usd_including_gst": "0.60",
                "per_case_cumulative_attempts": 6,
                "per_case_usd_including_gst": "0.30",
                "maximum_new_calls": {"email": 0, "calendar": 2},
            }
            if self.segment_path.exists():
                saved = json.loads(self.segment_path.read_text())
                if any(saved.get(k) != v for k, v in expected.items()):
                    raise BudgetExceeded("Resumption identity changed")
            else:
                if len(self.calls) != 10:
                    raise BudgetExceeded("Cannot reopen a window after resumed calls")
                saved = {**expected, "started_unix": time.time(), "halted": False}
                saved["deadline_unix"] = saved["started_unix"] + 900
                with self.segment_path.open("x") as stream:
                    json.dump(saved, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            if saved["deadline_unix"] != saved["started_unix"] + 900:
                raise BudgetExceeded("Invalid persisted deadline")
            self.segment = saved
            self.started -= max(0, time.time() - saved["started_unix"])
            self.halted = (
                saved["halted"]
                or any(c["outcome"] != "completed" for c in self.calls[10:])
                or any(p.get("error_type") for p in self.preflights[len(baseline["preflights"]) :])
            )
            self.save()
        except Exception:
            self.close()
            raise

    def validate_prefix(self):
        if not 10 <= len(self.calls) <= 12 or len(self.baseline["calls"]) != 10:
            raise BudgetExceeded("Original ten attempts or cumulative cap changed")
        if any(c["outcome"] != "completed" for c in self.baseline["calls"]):
            raise BudgetExceeded("Original completed receipts changed")
        if self.calls[:10] != self.baseline["calls"]:
            raise BudgetExceeded("Original call receipts changed")
        if self.preflights[: len(self.baseline["preflights"])] != self.baseline["preflights"]:
            raise BudgetExceeded("Original preflights changed")
        if any(c["scenario"] not in {"email", "calendar"} for c in self.calls):
            raise BudgetExceeded("Unapproved scenario")
        if any(
            sum(c["scenario"] == kind for c in self.calls) > 6 for kind in ("email", "calendar")
        ):
            raise BudgetExceeded("Per-case allowance exceeded")

    def save(self):
        self.validate_prefix()
        super().save()
        if getattr(self, "segment", None):
            self.segment["halted"] = self.halted
            write(self.segment_path, self.segment)

    def stop(self, reason):
        self.halted = True
        self.segment.update(stop_reason=reason, stopped_at=datetime.now(UTC).isoformat())
        self.save()

    def client(self, raw):
        guarded, budget = super().client(raw), self

        class Limited:
            def converse(self, **request):
                with budget.dispatch_lock:
                    if budget.scenario != "calendar":
                        raise BudgetExceeded("Unapproved scenario")
                    case = [c for c in budget.calls if c["scenario"] == budget.scenario]
                    if budget.halted or len(budget.calls) >= 12:
                        raise BudgetExceeded("Third-budget allowance exhausted/stopped")
                    if len(case) >= 6:
                        raise BudgetExceeded("Six-attempt case allowance exhausted")
                    if cost(case) + PER_ATTEMPT > Decimal("0.30"):
                        raise BudgetExceeded("Case GST-inclusive cost cap")
                    if cost(budget.calls) + PER_ATTEMPT > Decimal("0.60"):
                        raise BudgetExceeded("Combined GST-inclusive cost cap")
                    if budget.calls and budget.pause_seconds:
                        time.sleep(budget.pause_seconds)
                    before, started = len(budget.calls), datetime.now(UTC).isoformat()
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

        return Limited()


if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
    from sqlalchemy.engine import make_url

    target = make_url(os.environ["THREADLY_TEST_DB"])
    if (target.host, target.port, target.database) != (
        "127.0.0.1",
        55439,
        "threadly_context_eval3_final_test",
    ) or os.getenv("THREADLY_REQUIRE_TEST_DB") != "1":
        raise RuntimeError("Final evaluation requires its new isolated database")
    path = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != BASE_HASH:
        raise RuntimeError("Do not reseed a used resumption; preserve its DB and report")
    if any(
        (path.parent / name).exists()
        for name in (
            "chat-context-third-approved-budget-ledger.resume-context7.json",
            "chat-context-context7-resumed-model-report.json",
            "chat-context-context7-reviews",
        )
    ):
        raise RuntimeError("Resumption already started; preserve its database and evidence")


@pytest.mark.skipif(os.getenv("THREADLY_CONTEXT_EVAL") != APPROVAL, reason="No paid run by default")
async def test_approved_context7_resumption(configured, db_sessionmaker, monkeypatch):
    from botocore.client import BaseClient
    from sqlalchemy.engine import make_url

    from app.calendar import permissions
    from app.calendar import service as calendar_service
    from app.model_client import conversation

    assert assets()["release"] == RELEASE
    assert not subprocess.check_output(
        ["git", "diff", "--name-only", RUNTIME, "--", "app", "alembic"]
    ).strip()
    assert not subprocess.check_output(["git", "status", "--porcelain"]).strip()
    assert subprocess.run(["git", "merge-base", "--is-ancestor", EVIDENCE, "HEAD"]).returncode == 0
    url = make_url(os.environ["THREADLY_TEST_DB"])
    assert (url.host, url.port, url.database) == (
        "127.0.0.1",
        55439,
        "threadly_context_eval3_final_test",
    )
    assert os.environ["THREADLY_REQUIRE_TEST_DB"] == "1"
    assert os.environ["BEDROCK_MODEL_ID"] == APPROVED_MODEL
    assert os.environ["BEDROCK_REGION"] == APPROVED_REGION
    ledger = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    assert ledger.name == "chat-context-third-approved-budget-ledger.json"
    directory = ledger.parent
    destination = directory / "chat-context-context7-resumed-model-report.json"
    segment = directory / "chat-context-third-approved-budget-ledger.resume-context7.json"
    reviews = directory / "chat-context-context7-reviews"
    baseline = json.loads(ledger.read_text())
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == BASE_HASH
    assert cost(baseline["calls"]) == Decimal("0.34752047")
    assert not destination.exists() and not segment.exists() and not reviews.exists()
    identity_path = Path(str(ledger) + ".identity.json")
    identity_bytes = identity_path.read_bytes()
    for name, expected in PRIOR_HASHES.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    pricing = json.loads((directory / "chat-context-context7-resume-pricing.json").read_text())
    assert pricing["rates"]["input"]["price"] == "1.1000000000"
    assert pricing["rates"]["output"]["price"] == "5.5000000000"
    auth = json.loads((directory / "chat-context-context7-resume-auth.json").read_text())
    assert auth["auth"] == "success" and auth["expected_account"]
    for preflight in (pricing, auth):
        assert (
            datetime.now(UTC) - datetime.fromisoformat(preflight["checked_at"])
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
        from tests.test_goal_review import recorded_calendar

        previous, _ = await recorded_calendar(db_sessionmaker)
    seed_state = await record_state(db_sessionmaker, previous)
    prepare_runtime_clients(monkeypatch)
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, previous["conversation_id"]))
    request = turn(
        "Where do I confirm it?",
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
    write(
        directory / "chat-context-context7-resume-full-preflight.json",
        {
            **assets(),
            "at": datetime.now(UTC).isoformat(),
            "input_tokens": counted["inputTokens"],
            "request_id": counted["ResponseMetadata"]["RequestId"],
            "paid_calls": 0,
        },
    )
    reviews.mkdir()
    budget = Resume7Budget(ledger, segment, baseline)
    install_dispatch_guard(monkeypatch, budget)
    report = {
        **assets(),
        "segment": budget.segment,
        "calls": budget.calls,
        "runtime_head": RUNTIME,
        "harness_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "seed": {"response": previous, **seed_state},
        "scenarios": [],
        "calendar_calls": calendar_calls,
    }
    write(destination, report)
    try:
        cases = [
            ("calendar", previous, ["Where do I confirm it?", "Has the event been created yet?"]),
        ]
        for name, previous, texts in cases:
            if budget.halted:
                break
            budget.scenario = name
            entry = {"case": name, "actual_turns": []}
            report["scenarios"].append(entry)
            for index, text in enumerate(texts):
                if budget.halted or sum(c["scenario"] == name for c in budget.calls) >= 6:
                    entry["stopped"] = "case_limit_or_halted"
                    break
                item = {"index": index, "user": text, "optional": name == "email" and index >= 2}
                entry["actual_turns"].append(item)
                try:
                    previous = await asyncio.wait_for(
                        step(db_sessionmaker, previous, text, conversation.ConversationModel()),
                        timeout=120,
                    )
                    item["response"] = previous
                    item.update(await record_state(db_sessionmaker, previous))
                except Exception as exc:
                    item["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
                    budget.stop("turn_or_provider_error")
                digest = hashlib.sha256(
                    json.dumps(item, sort_keys=True, default=str).encode()
                ).hexdigest()
                item["review_sha256"] = digest
                report["awaiting_review"] = {"case": name, "index": index, "sha256": digest}
                write(destination, report)
                print(
                    json.dumps(
                        {
                            "review": report["awaiting_review"],
                            "cumulative_attempts": len(budget.calls),
                        }
                    ),
                    flush=True,
                )
                review_path = reviews / f"{name}-{index}.json"
                while not budget.halted and not review_path.exists():
                    if time.time() >= budget.segment["deadline_unix"]:
                        budget.stop("review_window_expired")
                        break
                    await asyncio.sleep(0.25)
                if budget.halted:
                    break
                review = json.loads(review_path.read_text())
                assert review["sha256"] == digest and review["decision"] in {
                    "continue",
                    "stop",
                    "next_case",
                }
                item["review"] = review
                report.pop("awaiting_review", None)
                if review["decision"] == "stop":
                    budget.stop("semantic_regression")
                write(destination, report)
                if review["decision"] != "continue":
                    break
    finally:
        if not budget.halted:
            budget.stop("planned_cases_complete_or_case_caps")
        report.update(
            finished_at=datetime.now(UTC).isoformat(),
            cumulative_attempts=len(budget.calls),
            cumulative_cost_including_gst=str(cost(budget.calls)),
            unused_attempts=12 - len(budget.calls),
            halted=budget.halted,
        )
        write(destination, report)
        budget.validate_prefix()
        budget.close()
        assert identity_path.read_bytes() == identity_bytes
        for name, expected in PRIOR_HASHES.items():
            assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
        get_settings.cache_clear()
