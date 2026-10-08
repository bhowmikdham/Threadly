"""Second, separately approved synthetic diagnostic. Never collected by normal pytest.

The exhausted first ledger is immutable. Scripted seeds are disclosed separately;
only decisions after those seeds are evaluated with the real model.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.conftest import db_engine, db_sessionmaker  # noqa: F401

# ruff: noqa: E402, I001
from app.api.errors import ApiError
from app.assistant import source_data, worker
from app.config import get_settings
from app.conversation import service, store
from app.conversation.prompt import assets
from app.db.models import ActionJob, ArtifactRevision, AssistantAction, AssistantTask, Conversation
from tests.test_calendar_creation import ready, turn
from tests.test_conversation import Model, configured, tool  # noqa: F401
from tests.test_on_demand_gmail import MID, TID, setup  # noqa: F401
from tests.test_on_demand_gmail import Model as Generator
from tools.evaluate_chat_context import (
    APPROVED_MODEL,
    APPROVED_REGION,
    Budget,
    BudgetExceeded,
    install_dispatch_guard,
    prepare_runtime_clients,
)

APPROVAL = "approved-second-18-calls-synthetic-context-3"
RELEASE = "contextual-conversation-1.8.9+chat-context.3"
BATCH = "second-approved-context-evaluation-2026-10-07"

if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
    # Fail at collection, before destructive test DB fixtures can reseed a used run.
    prior = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    if prior.exists() and json.loads(prior.read_text())["calls"]:
        raise RuntimeError("Used second ledger: preserve DB and resume its pending turn explicitly")


def scenarios():
    return [
        {
            "id": "corrected_draft_completion",
            "setup": "One scripted unfinished Alex draft; no generated draft yet.",
            "turns": [
                "Another email for Casey, please.",
                "Back to Alex: ask whether the sapphire crate has arrived.",
                "Now Casey's one: thank them for the map.",
            ],
            "rubric": "Separate Alex/Casey goals; useful generated text for both; no writes.",
        },
        {
            "id": "calendar_preview_revision",
            "setup": "Empty owned chat, synthetic Calendar preferences, Ask approval mode.",
            "turns": [
                "Put Quiet hour in my diary tomorrow at 2 pm.",
                "Make it 3 pm instead.",
                "Thanks, that's all.",
            ],
            "rubric": "Correct preview/revision; retain original day/title; no approval/job.",
        },
        {
            "id": "saved_draft_resumption",
            "setup": "Scripted source-based saved reply, then a separate unfinished Calendar goal.",
            "turns": ["Set that aside and bring back the saved agenda reply draft."],
            "rubric": "Return existing task/artifact; retain Calendar goal; no regeneration.",
        },
    ]


class FollowupBudget(Budget):
    """One durable batch identity and wall-clock deadline, including process restarts."""

    def __init__(self, ledger_path):
        super().__init__(ledger_path)
        path = Path(str(ledger_path) + ".identity.json")
        identity = {"batch": BATCH, "release": RELEASE, "assets": assets()}
        try:
            if path.exists():
                saved = json.loads(path.read_text())
                if any(saved.get(key) != value for key, value in identity.items()):
                    raise BudgetExceeded("Second evaluation identity changed")
            else:
                if self.calls:
                    raise BudgetExceeded("Existing paid ledger has no second-batch identity")
                saved = {**identity, "started_unix": time.time()}
                with path.open("x") as stream:
                    json.dump(saved, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.started -= max(0, time.time() - saved["started_unix"])
        except Exception:
            self.close()
            raise

    def client(self, raw):
        guarded = super().client(raw)
        budget = self

        class Dated:
            def converse(self, **request):
                # Lower the burst rate after the first batch's throttle; no retry.
                if (
                    budget.calls
                    and not budget.halted
                    and len(budget.calls) < budget.max_calls
                    and time.monotonic() - budget.started < budget.max_seconds
                ):
                    time.sleep(8)
                before = len(budget.calls)
                started = datetime.now(UTC).isoformat()
                try:
                    return guarded.converse(**request)
                finally:
                    if len(budget.calls) > before:
                        budget.calls[-1].update(
                            started_at=started, finished_at=datetime.now(UTC).isoformat()
                        )
                        budget.save()

            def close(self):
                guarded.close()

        return Dated()


async def step(factory, previous, text, model, **context):
    values = (
        {}
        if previous is None
        else {
            "conversation_id": previous["conversation_id"],
            "expected_version": previous["version"],
        }
    )
    async with source_data.source_scope():
        return await service.turn(1, turn(text, **values, **context), factory=factory, model=model)


async def seed_cases(factory):
    """All setup uses scripted adapters; callers prohibit AWS during this phase."""
    text = "Can you help with an email for Alex?"
    alex = await step(
        factory,
        None,
        text,
        Model(
            tool(
                "prepare_email_draft",
                recipient="Alex",
                request_source=text,
            )
        ),
    )
    assert alex["kind"] == "clarification"
    async with source_data.source_scope():
        await source_data.fetch(1, TID)
        async with factory.begin() as db:
            captured = await source_data.capture(db, 1, TID, message_id=MID)
        text = "Draft a reply"
        reply = await step(
            factory,
            None,
            text,
            Model(
                tool("read_email", reference="selected"),
                tool("prepare_workflow", intent="reply", reference="selected", request_source=text),
            ),
            context_snapshot_id=captured.id,
        )
    assert reply["kind"] == "task", reply
    assert await worker.run_once(factory, Generator())
    reply = await step(
        factory,
        reply,
        "Use sender@example.test",
        Model(
            tool(
                "answer_question",
                answer={"recipients": ["sender@example.test"]},
            )
        ),
    )
    assert await worker.run_once(factory, Generator())
    async with factory() as db:
        task = await db.get(AssistantTask, reply["task_id"])
        assert task.state == "succeeded" and task.final_artifact_id, (task.state, task.error_code)
        artifact = await db.get(ArtifactRevision, task.final_artifact_id)
        saved = {"task_id": task.id, "artifact_id": artifact.id, "artifact": artifact.payload}
    detour = await step(
        factory,
        reply,
        "Create Focus tomorrow",
        Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
            )
        ),
    )
    assert detour["kind"] == "clarification", detour
    return [
        {"previous": alex, "seed_responses": [alex]},
        {"previous": None, "seed_responses": []},
        {"previous": detour, "seed_responses": [reply, detour], "saved_artifact": saved},
    ]


async def prepare_calendar_account(factory):
    from app.auth.google import CALENDAR_SCOPES
    from app.db.models import User

    await ready(factory)
    async with factory.begin() as db:
        user = await db.get(User, 1)
        user.google_scopes = [*user.google_scopes, *CALENDAR_SCOPES]


def install_calendar_fake(monkeypatch, calls):
    from app.calendar import client

    original = client._request

    def fake(request):
        calls.append({"method": request.method, "path": request.url.path})
        if request.method == "GET" and request.url.path.endswith("calendarList"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "work@example.test",
                            "summary": "Work",
                            "primary": True,
                            "accessRole": "owner",
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("freeBusy"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "timeMin": body["timeMin"],
                    "timeMax": body["timeMax"],
                    "calendars": {item["id"]: {"busy": []} for item in body["items"]},
                },
            )
        if request.method == "GET" and request.url.path.endswith("events"):
            return httpx.Response(200, json={"items": []})
        raise AssertionError("Unknown Calendar operation/mutation blocked")

    async def request(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake)
        return await original(*args, **kwargs)

    monkeypatch.setattr(client, "_request", request)


async def record_state(factory, response):
    from sqlalchemy import func, select
    from app.conversation import goals
    from app.conversation.runtime import Runtime

    async with factory() as db:
        chat = await db.get(Conversation, response["conversation_id"])
        state = store.decode(chat)
        action_jobs = await db.scalar(select(func.count()).select_from(ActionJob))
        approved = await db.scalar(
            select(func.count())
            .select_from(AssistantAction)
            .where(AssistantAction.state.in_(["approved", "running", "succeeded"]))
        )
        assert action_jobs == approved == 0
        saved = {"state": state, "action_jobs": action_jobs, "approved_actions": approved}
        if response.get("task_id"):
            task = await db.get(AssistantTask, response["task_id"])
            saved["task"] = {
                "id": task.id,
                "state": task.state,
                "artifact_id": task.final_artifact_id,
                "error_code": task.error_code,
            }
            if task.final_artifact_id:
                saved["task"]["artifact"] = (
                    await db.get(ArtifactRevision, task.final_artifact_id)
                ).payload
    runtime = Runtime(1, turn("Inspect synthetic state", conversation_id=chat.id), state, factory)
    saved["goals"] = await goals.listing(runtime)
    return saved


@pytest.mark.skipif(
    os.getenv("THREADLY_CONTEXT_EVAL") != APPROVAL,
    reason="Separate second-batch approval required; no live default",
)
async def test_live_followup(configured, db_sessionmaker, monkeypatch):  # noqa: F811
    from botocore.client import BaseClient
    from sqlalchemy.engine import make_url
    from app.calendar import permissions
    from app.model_client.client import ModelClient
    from app.model_client.conversation import ConversationModel

    assert assets()["release"] == RELEASE
    url = make_url(os.environ["THREADLY_TEST_DB"])
    assert (url.host, url.port, url.database) == ("127.0.0.1", 55439, "threadly_context_eval2_test")
    assert os.environ["THREADLY_REQUIRE_TEST_DB"] == "1"
    assert os.environ["BEDROCK_MODEL_ID"] == APPROVED_MODEL
    assert os.environ["BEDROCK_REGION"] == APPROVED_REGION
    ledger = Path(os.environ["THREADLY_CONTEXT_EVAL_LEDGER"])
    assert ledger.name == "chat-context-second-approved-budget-ledger.json"
    # A rerun cannot overwrite the first report or silently reseed used paid cases.
    destination = Path(os.environ["THREADLY_CONTEXT_EVAL_REPORT"])
    assert destination.name == "chat-context-second-approved-model-report.json"
    if ledger.exists():
        assert not json.loads(ledger.read_text())["calls"], "Use explicit pending-turn recovery"
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
    await prepare_calendar_account(db_sessionmaker)
    calendar_calls = []
    install_calendar_fake(monkeypatch, calendar_calls)

    async def no_http(*args, **kwargs):
        raise AssertionError("Non-mocked HTTP disabled")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_http)
    with monkeypatch.context() as seed_guard:

        def no_aws(*args, **kwargs):
            raise AssertionError("All AWS calls forbidden during scripted setup")

        seed_guard.setattr(BaseClient, "_make_api_call", no_aws)
        seeds = await seed_cases(db_sessionmaker)
    prepare_runtime_clients(monkeypatch)
    budget = FollowupBudget(ledger)
    install_dispatch_guard(monkeypatch, budget)
    report = {
        **assets(),
        "batch": BATCH,
        "started_at": datetime.now(UTC).isoformat(),
        "model_id": APPROVED_MODEL,
        "region": APPROVED_REGION,
        "calls": budget.calls,
        "preflights": budget.preflights,
        "scenarios": [],
        "calendar_calls": calendar_calls,
        "human_review_required": True,
    }
    try:
        for case, seed in zip(scenarios(), seeds, strict=True):
            if budget.halted:
                break
            budget.scenario = case["id"]
            entry = {**case, "scripted_setup": seed, "actual_turns": []}
            report["scenarios"].append(entry)
            previous = seed["previous"]
            for text in case["turns"]:
                if budget.halted or sum(c["scenario"] == case["id"] for c in budget.calls) >= 6:
                    entry["stopped"] = "scenario_or_provider_limit"
                    break
                item = {"user": text}
                entry["actual_turns"].append(item)
                try:
                    previous = await step(db_sessionmaker, previous, text, ConversationModel())
                    item["response"] = previous
                    if previous.get("task_id"):
                        item["worker_ran"] = await worker.run_once(db_sessionmaker, ModelClient())
                    item.update(await record_state(db_sessionmaker, previous))
                except ApiError as exc:
                    item["error"] = {"code": exc.code, "status": exc.status}
                    break
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        destination.write_text(json.dumps(report, indent=2, default=str) + "\n")
        budget.close()
        get_settings.cache_clear()
