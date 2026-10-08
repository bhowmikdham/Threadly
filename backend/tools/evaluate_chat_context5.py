"""Approved third diagnostic: .5, twelve attempts/$0.60 with GST, no Google writes."""
# ruff: noqa: E402, F401, F811

import asyncio
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import budget as context_budget
from app.conversation import store
from app.conversation.prompt import PROMPT, assets
from app.conversation.runtime import Runtime
from app.db.models import Conversation
from app.pii.masking import mask_structure
from app.schemas.conversation import tool_config
from tests.conftest import db_engine, db_sessionmaker
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
from tools.evaluate_chat_context_followup import (
    install_calendar_fake,
    prepare_calendar_account,
    record_state,
    scenarios,
    step,
)
from tools.resume_chat_context_evaluation import PER_ATTEMPT, cost

APPROVAL = "approved-context5-twelve-060-gst-20261008"
MESSAGE = "Sentinel_3973d49c351881918d50d28411bca36c"
RELEASE = "contextual-conversation-1.8.9+chat-context.5"
RUNTIME = "7fb055f409c46479bc1cd1194aa870ff93ccf187"
FRONTEND = "86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf"
FIRST_HASH = "9bc8946f9c9cb4c7bbdceafc1342cb78ceaca2d8227afcd6cbe3a5e71927245a"
SECOND_HASH = "7aa0b884d78d66620c3712bb077cbfc328b9e7e75d0735db0cc8416c6eb6e015"
PRIOR_HASHES = {
    "chat-context-approved-budget-ledger.json": FIRST_HASH,
    "chat-context-second-approved-budget-ledger.json": SECOND_HASH,
}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


class Context5Budget(Budget):
    max_calls = 12

    def __init__(self, ledger, *, pause_seconds=8):
        super().__init__(ledger)
        self.dispatch_lock = threading.Lock()
        self.pause_seconds = pause_seconds
        path = Path(str(ledger) + ".identity.json")
        expected = {
            "approval": MESSAGE,
            "approved_at": "2026-10-08T00:40:11Z",
            "batch": APPROVAL,
            "release": RELEASE,
            "assets": assets(),
            "runtime": RUNTIME,
            "maximum_attempts": 12,
            "per_case_attempts": 6,
            "maximum_usd_including_gst": "0.60",
            "per_case_usd_including_gst": "0.30",
        }
        try:
            if path.exists():
                if not self.ledger_path.exists():
                    raise BudgetExceeded("Missing used ledger; cannot reset allowance")
                saved = json.loads(path.read_text())
                if any(saved.get(k) != v for k, v in expected.items()):
                    raise BudgetExceeded("Approved batch identity changed")
            else:
                if self.calls:
                    raise BudgetExceeded("Used ledger has no approved identity")
                saved = {**expected, "started_unix": time.time()}
                saved["deadline_unix"] = saved["started_unix"] + 900
                with path.open("x") as stream:
                    json.dump(saved, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            if saved["deadline_unix"] != saved["started_unix"] + 900:
                raise BudgetExceeded("Invalid persisted window")
            self.identity = saved
            self.started -= max(0, time.time() - saved["started_unix"])
            self.halted = any(c["outcome"] != "completed" for c in self.calls) or any(
                p.get("error_type") for p in self.preflights
            )
            self.save()
        except Exception:
            self.close()
            raise

    def client(self, raw):
        guarded = super().client(raw)
        budget = self

        class Limited:
            def converse(self, **request):
                with budget.dispatch_lock:
                    if budget.scenario not in {"email", "calendar"}:
                        raise BudgetExceeded("Unapproved scenario")
                    case = [c for c in budget.calls if c["scenario"] == budget.scenario]
                    if budget.halted or len(budget.calls) >= 12:
                        raise BudgetExceeded("Twelve-attempt allowance exhausted/stopped")
                    if len(case) >= 6:
                        raise BudgetExceeded("Six-attempt case allowance exhausted")
                    if cost(case) + PER_ATTEMPT > Decimal("0.30"):
                        raise BudgetExceeded("Case GST-inclusive cost cap")
                    if cost(budget.calls) + PER_ATTEMPT > Decimal("0.60"):
                        raise BudgetExceeded("Combined GST-inclusive cost cap")
                    if budget.calls and budget.pause_seconds:
                        time.sleep(budget.pause_seconds)
                    before = len(budget.calls)
                    started = datetime.now(UTC).isoformat()
                    try:
                        return guarded.converse(**request)
                    finally:
                        if len(budget.calls) > before:
                            budget.calls[-1].update(
                                started_at=started,
                                finished_at=datetime.now(UTC).isoformat(),
                                release=RELEASE,
                            )
                            budget.save()

        return Limited()


if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
    ledger = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    if ledger.exists():
        raise RuntimeError("Do not reseed an existing diagnostic; preserve ledger and database")


@pytest.mark.skipif(os.getenv("THREADLY_CONTEXT_EVAL") != APPROVAL, reason="No paid run by default")
async def test_approved_context5(configured, db_sessionmaker, monkeypatch):
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
    url = make_url(os.environ["THREADLY_TEST_DB"])
    assert (url.host, url.port, url.database) == ("127.0.0.1", 55439, "threadly_context_eval3_test")
    assert os.environ["THREADLY_REQUIRE_TEST_DB"] == "1"
    assert os.environ["BEDROCK_MODEL_ID"] == APPROVED_MODEL
    assert os.environ["BEDROCK_REGION"] == APPROVED_REGION
    ledger = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    assert ledger.name == "chat-context-third-approved-budget-ledger.json"
    directory = ledger.parent
    destination = directory / "chat-context-context5-model-report.json"
    reviews = directory / "chat-context-context5-reviews"
    assert not destination.exists() and not reviews.exists()
    for name, expected in PRIOR_HASHES.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    pricing = json.loads((directory / "chat-context-context5-pricing-raw.json").read_text())
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
        first = "Can you help with an email for Alex?"
        alex = await step(
            db_sessionmaker,
            None,
            first,
            Model(tool("start_email_draft", recipient="Alex", request_source=first)),
        )
    assert alex["kind"] == "clarification"
    seed_state = await record_state(db_sessionmaker, alex)
    prepare_runtime_clients(monkeypatch)
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, alex["conversation_id"]))
    request = turn(
        scenarios()[0]["turns"][0],
        conversation_id=alex["conversation_id"],
        expected_version=alex["version"],
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
        directory / "chat-context-context5-full-preflight.json",
        {
            **assets(),
            "at": datetime.now(UTC).isoformat(),
            "input_tokens": counted["inputTokens"],
            "request_id": counted["ResponseMetadata"]["RequestId"],
            "paid_calls": 0,
        },
    )
    reviews.mkdir()
    budget = Context5Budget(ledger)
    install_dispatch_guard(monkeypatch, budget)
    report = {
        **assets(),
        "identity": budget.identity,
        "calls": budget.calls,
        "harness_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "application_runtime_head": RUNTIME,
        "frontend_head": FRONTEND,
        "seed": {"response": alex, **seed_state},
        "scenarios": [],
        "calendar_calls": calendar_calls,
    }
    write(destination, report)
    try:
        cases = [
            (
                "email",
                alex,
                scenarios()[0]["turns"]
                + [
                    "A separate note for Morgan, please.",
                    "Back to Casey's draft: make it a little warmer.",
                ],
            ),
            ("calendar", None, scenarios()[1]["turns"] + ["Where do I confirm it?"]),
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
                item = {"index": index, "user": text, "optional": index >= 3}
                entry["actual_turns"].append(item)
                try:
                    previous = await step(
                        db_sessionmaker, previous, text, conversation.ConversationModel()
                    )
                    item["response"] = previous
                    item.update(await record_state(db_sessionmaker, previous))
                except Exception as exc:
                    item["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
                    budget.halted = True
                digest = hashlib.sha256(
                    json.dumps(item, sort_keys=True, default=str).encode()
                ).hexdigest()
                item["review_sha256"] = digest
                report["awaiting_review"] = {"case": name, "index": index, "sha256": digest}
                write(destination, report)
                print(
                    json.dumps(
                        {"review": report["awaiting_review"], "attempts": len(budget.calls)}
                    ),
                    flush=True,
                )
                review_path = reviews / f"{name}-{index}.json"
                while not budget.halted and not review_path.exists():
                    if time.time() >= budget.identity["deadline_unix"]:
                        budget.halted = True
                        entry["stopped"] = "review_window_expired"
                        break
                    await asyncio.sleep(0.25)
                if budget.halted:
                    break
                review = json.loads(review_path.read_text())
                assert review["sha256"] == digest
                assert review["decision"] in {"continue", "stop", "next_case"}
                item["review"] = review
                report.pop("awaiting_review", None)
                if review["decision"] == "stop":
                    budget.halted = True
                write(destination, report)
                if review["decision"] != "continue":
                    break
    finally:
        report.update(
            finished_at=datetime.now(UTC).isoformat(),
            attempts=len(budget.calls),
            cost_including_gst=str(cost(budget.calls)),
            remaining_attempts=12 - len(budget.calls),
            halted=budget.halted,
        )
        write(destination, report)
        budget.close()
        get_settings.cache_clear()
        for name, expected in PRIOR_HASHES.items():
            assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
