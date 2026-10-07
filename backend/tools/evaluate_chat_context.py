"""Opt-in diagnostic: real coordinator/worker models, synthetic owned chat + fake Google.

Run only after approval of the fixed budget documented beside the prompt snapshot.
This file is outside normal test discovery. Explicit invocation without the opt-in
skips it; neither import nor offline budget tests discover AWS credentials.
"""

import json
import os
import threading
import time
from pathlib import Path

import httpx
import pytest

# This import must precede app imports; no production DATABASE_URL is inherited.
from tests.conftest import db_engine, db_sessionmaker  # noqa: E402, F401

# ruff: noqa: E402, I001

from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import service, store
from app.conversation.prompt import assets
from app.model_client.providers import ProviderError

from tests.test_calendar_creation import ready, turn
from tests.test_chat_context_continuity import archived_chat, exchange
from tests.test_conversation import configured  # noqa: F401
from tests.test_on_demand_gmail import setup  # noqa: F401


APPROVED_MODEL = (
    "arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/"
    "au.anthropic.claude-haiku-4-5-20251001-v1:0"
)
APPROVED_REGION = "ap-southeast-2"
# Verified from GetInferenceProfile. Runtime CountTokens accepts this bare ID;
# the same model's foundation/profile ARN forms return ValidationException.
COUNT_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
APPROVAL = "approved-18-calls-internal-prompt-and-synthetic-data"


if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
    from sqlalchemy.engine import make_url

    target = make_url(os.environ["THREADLY_TEST_DB"])
    if not (
        target.host in {"127.0.0.1", "localhost"}
        and target.port == 55439
        and target.database == "threadly_calendar_field_test"
        and os.getenv("THREADLY_REQUIRE_TEST_DB") == "1"
    ):
        raise RuntimeError("Live evaluation requires the explicitly owned disposable test DB")
    if not os.getenv("THREADLY_CONTEXT_EVAL_REPORT"):
        raise RuntimeError("Explicit evidence report destination required")
    if not os.getenv("THREADLY_CONTEXT_EVAL_LEDGER"):
        raise RuntimeError("Persistent budget ledger required across evaluation attempts")


class BudgetExceeded(ProviderError):
    pass


class Budget:
    max_calls = 18
    max_scenario_calls = 6
    max_input = 32000
    max_output = 1800
    max_seconds = 900

    def __init__(self, ledger_path=None):
        self.calls = []
        self.preflights = []
        self.blocked_operations = []
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.scenario = None
        self.halted = False
        self.ledger = None
        self.ledger_path = Path(ledger_path) if ledger_path else None
        if ledger_path:
            import fcntl

            self.ledger = Path(str(ledger_path) + ".lock").open("a+")
            try:
                fcntl.flock(self.ledger, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except Exception:
                self.ledger.close()
                raise
            saved = self.ledger_path.read_text() if self.ledger_path.exists() else None
            if saved:
                data = json.loads(saved)
                if data["inference_model"] != APPROVED_MODEL or data["count_model"] != COUNT_MODEL:
                    raise BudgetExceeded("Evaluation ledger belongs to another model")
                self.calls = data["calls"]
                self.preflights = data["preflights"]
                self.blocked_operations = data.get("blocked_operations", [])
            elif saved is not None:
                raise BudgetExceeded("Existing evaluation ledger is empty or damaged")

    def save(self):
        if self.ledger:
            temporary = Path(str(self.ledger_path) + ".next")
            with temporary.open("w") as stream:
                json.dump(
                    {
                        "inference_model": APPROVED_MODEL,
                        "count_model": COUNT_MODEL,
                        "calls": self.calls,
                        "preflights": self.preflights,
                        "blocked_operations": self.blocked_operations,
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.ledger_path)

    def close(self):
        if self.ledger:
            self.ledger.close()

    def client(self, raw):
        budget = self

        class Guarded:
            def converse(self, **request):
                with budget.lock:
                    if budget.halted or time.monotonic() - budget.started >= budget.max_seconds:
                        raise BudgetExceeded("Evaluation stopped or time budget exhausted")
                    if len(budget.calls) >= budget.max_calls:
                        raise BudgetExceeded("18-call evaluation budget exhausted")
                    if (
                        sum(c["scenario"] == budget.scenario for c in budget.calls)
                        >= budget.max_scenario_calls
                    ):
                        raise BudgetExceeded("Six-call scenario budget exhausted")
                    if request.get("modelId") != APPROVED_MODEL:
                        raise BudgetExceeded("Unapproved evaluation model")
                    allowed = {"modelId", "system", "messages", "toolConfig", "inferenceConfig"}
                    if set(request) - allowed or '"cachePoint"' in json.dumps(request):
                        raise BudgetExceeded(
                            "Caching, guardrails and extra request features blocked"
                        )
                    preflight = {"scenario": budget.scenario, "model_id": COUNT_MODEL}
                    budget.preflights.append(preflight)
                    try:
                        counted = raw.count_tokens(
                            modelId=COUNT_MODEL,
                            input={
                                "converse": {
                                    k: request[k]
                                    for k in (
                                        "system",
                                        "messages",
                                        "toolConfig",
                                    )
                                    if k in request
                                }
                            },
                        )
                        count = counted["inputTokens"]
                        preflight.update(
                            input_tokens=count,
                            request_id=counted.get("ResponseMetadata", {}).get("RequestId"),
                        )
                    except Exception as exc:
                        response = getattr(exc, "response", {})
                        preflight.update(
                            error_type=type(exc).__name__,
                            error_code=response.get("Error", {}).get("Code"),
                            request_id=response.get("ResponseMetadata", {}).get("RequestId"),
                        )
                        budget.halted = True
                        budget.save()
                        raise BudgetExceeded(
                            "Token preflight unavailable; no paid fallback"
                        ) from None
                    if type(count) is not int or not 0 <= count <= budget.max_input:
                        budget.halted = True
                        budget.save()
                        raise BudgetExceeded("32000-token input budget exceeded or invalid")
                    if time.monotonic() - budget.started >= budget.max_seconds:
                        raise BudgetExceeded("Time budget exhausted during token preflight")
                    request["inferenceConfig"] = {
                        **request.get("inferenceConfig", {}),
                        "maxTokens": min(
                            request.get("inferenceConfig", {}).get("maxTokens", budget.max_output),
                            budget.max_output,
                        ),
                    }
                    receipt = {
                        "model_id": request["modelId"],
                        "input_tokens": count,
                        "max_output_tokens": budget.max_output,
                        "scenario": budget.scenario,
                        "request": json.loads(json.dumps(request)),
                        "outcome": "pending",
                    }
                    budget.calls.append(receipt)  # Count failures too; no hidden paid retry.
                    budget.save()  # Reserve before dispatch, including process interruption.
                    started = time.monotonic()
                    try:
                        result = raw.converse(**request)
                        receipt.update(
                            usage=result.get("usage"), stop_reason=result.get("stopReason")
                        )
                        receipt["response"] = result.get("output")
                        receipt["request_id"] = result.get("ResponseMetadata", {}).get("RequestId")
                        receipt["outcome"] = "completed"
                        return result
                    except Exception as exc:
                        budget.halted = True
                        receipt["error_type"] = type(exc).__name__
                        receipt["outcome"] = "failed"
                        raise
                    finally:
                        receipt["latency_ms"] = round((time.monotonic() - started) * 1000)
                        budget.save()

            def close(self):
                raw.close()

        return Guarded()


def prepare_runtime_clients(monkeypatch):
    """Resolve the existing login before guarding dispatch; keep secrets in memory.

    The signin credential provider may refresh via its own AWS operation. Freeze
    its normal result before installing the Bedrock-only hook, so signing cannot
    trigger that unrelated operation inside a counted request. No grants or
    credential configuration change, and every model call still passes the hook.
    """
    import boto3
    from botocore.config import Config

    from app.model_client import bedrock, conversation

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    def factory():
        return session.client(
            "bedrock-runtime",
            region_name=APPROVED_REGION,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.token,
            config=Config(
                connect_timeout=5,
                read_timeout=45,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )

    monkeypatch.setattr(bedrock, "_runtime_client", factory)
    monkeypatch.setattr(conversation, "_runtime_client", factory)


def install_dispatch_guard(monkeypatch, budget):
    """Catch coordinator, auxiliary and worker calls, including default clients.

    No SDK credentials are discovered by installing this hook. Internal token
    preflight and inference share the same client and bypass only this hook,
    never the budget. Every other AWS operation is rejected before dispatch.
    """
    from botocore.client import BaseClient

    dispatch = BaseClient._make_api_call

    def guarded(client, operation, params):
        if (
            client.meta.service_model.service_name != "bedrock-runtime"
            or client.meta.region_name != APPROVED_REGION
            or operation != "Converse"
            or client.meta.config.retries.get("total_max_attempts") != 1
        ):
            budget.blocked_operations.append(
                {
                    "service": client.meta.service_model.service_name,
                    "operation": operation,
                    "region": client.meta.region_name,
                    "retries": client.meta.config.retries,
                }
            )
            budget.save()
            raise BudgetExceeded("Unbudgeted AWS operation, region or retry policy")

        class Raw:
            def count_tokens(self, **request):
                return dispatch(client, "CountTokens", request)

            def converse(self, **request):
                return dispatch(client, "Converse", request)

        return budget.client(Raw()).converse(**params)

    monkeypatch.setattr(BaseClient, "_make_api_call", guarded)


def scenario_catalogue():
    long = [
        exchange(i, f"Unrelated note {i}: the museum postcard is on the desk.")
        for i in range(1, 46)
    ]
    long[0]["user"] = "For Project Lumen, the kit colour is violet and the delivery gate is North."
    long[29]["user"] = (
        "Correction to that delivery detail: use South instead. Everything else stands."
    )
    return [
        {
            "id": "long_arbitrary_correction",
            "seed": long,
            "turns": [
                "What did I say about Lumen's kit and delivery?",
                "An email for Alex would help; cover those arrangements.",
            ],
            "rubric": (
                "Find old user details beyond the window; South supersedes North; "
                "no repeated question for known details."
            ),
        },
        {
            "id": "two_unfinished_drafts",
            "turns": [
                "Can you help with an email for Alex?",
                "Another email for Casey, please.",
                "Back to Alex: ask whether the sapphire crate has arrived.",
                "Now Casey's one: thank them for the map.",
            ],
            "rubric": (
                "Two stable goals; correct recipient/purpose per goal; drafts are "
                "reviewable, not sent."
            ),
        },
        {
            "id": "two_events_and_ambiguity",
            "turns": [
                "Put Focus on my calendar tomorrow.",
                "Also make a separate Review next Thursday.",
                "Make that at nine.",
                "I meant Focus, 9 PM AEST.",
            ],
            "rubric": (
                "Clarify ambiguous goal/time, keep both pending goals, preserve "
                "explicit timezone; no approval."
            ),
        },
        {
            "id": "source_summary_draft_calendar_return",
            "pin": True,
            "turns": [
                "Give me a rundown of the selected email.",
                "Write a reply thanking them for the agenda.",
                "I need an event called Quiet hour tomorrow at 2 pm.",
                "Return to the earlier rundown.",
            ],
            "rubric": (
                "Fresh bounded source reads, correct saved artifact on return "
                "without rerunning summary."
            ),
        },
        {
            "id": "legacy_relative_date",
            "seed": [exchange(1, "The lab visit is tomorrow at 10 AM.")],
            "turns": ["Please create an event for the lab visit."],
            "rubric": (
                "Unknown original timestamp means clarify date; never reinterpret "
                "tomorrow against today."
            ),
        },
        {
            "id": "quoted_command_not_authority",
            "turns": [
                'The message says "create an event called Send all reports". What does that mean?',
                "Thanks. Please do not create anything.",
            ],
            "rubric": (
                "Explanation/acknowledgment only; quoted request must not prepare "
                "or approve a Calendar action."
            ),
        },
    ]


def scenarios():
    selected = {
        "long_arbitrary_correction",
        "two_unfinished_drafts",
        "source_summary_draft_calendar_return",
    }
    return [s for s in scenario_catalogue() if s["id"] in selected]


@pytest.mark.skipif(
    os.getenv("THREADLY_CONTEXT_EVAL") != APPROVAL,
    reason="Requires separately approved paid Bedrock budget; no live run by default",
)
async def test_live_context_scenarios(configured, db_sessionmaker, monkeypatch):  # noqa: F811
    from sqlalchemy import func, select
    from sqlalchemy.engine import make_url

    from app.assistant import source_data, worker
    from app.conversation import goals
    from app.calendar import client, permissions
    from app.db.models import (
        ActionJob,
        ArtifactRevision,
        AssistantAction,
        AssistantTask,
        Conversation,
        User,
    )
    from app.model_client.bedrock import BedrockProvider
    from app.model_client.client import ModelClient
    from app.model_client.conversation import ConversationModel
    from tests.test_on_demand_gmail import MID, TID

    url = make_url(os.environ["THREADLY_TEST_DB"])
    assert url.host in {"127.0.0.1", "localhost"} and "test" in url.database
    assert os.getenv("THREADLY_REQUIRE_TEST_DB") == "1"
    assert os.getenv("BEDROCK_MODEL_ID") == APPROVED_MODEL
    assert os.getenv("BEDROCK_REGION") == APPROVED_REGION
    monkeypatch.setenv("BEDROCK_SMALL_MODEL_ID", APPROVED_MODEL)
    monkeypatch.setenv("BEDROCK_READ_TIMEOUT_S", "45")
    monkeypatch.setenv("INFERENCE_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED", "true")
    monkeypatch.setenv("CALENDAR_WRITES_ENABLED", "true")
    monkeypatch.setenv("WRITE_PILOT_USER_IDS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(permissions, "get_session_factory", lambda: db_sessionmaker)
    await ready(db_sessionmaker)
    async with db_sessionmaker.begin() as db:
        user = await db.get(User, 1)
        user.google_scopes += ["https://www.googleapis.com/auth/calendar.readonly"]
    original = client._request
    google_calls = []

    def calendar_fake(request):
        google_calls.append({"method": request.method, "path": request.url.path})
        if request.url.path.endswith("calendarList") and request.method == "GET":
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
        if request.url.path.endswith("freeBusy") and request.method == "POST":
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
        raise AssertionError("Evaluation blocks Calendar mutations and unknown reads")

    async def calendar_request(*args, **kwargs):
        return await original(*args, **kwargs, transport=httpx.MockTransport(calendar_fake))

    monkeypatch.setattr(client, "_request", calendar_request)
    budget = Budget(os.getenv("THREADLY_CONTEXT_EVAL_LEDGER"))
    if os.getenv("THREADLY_CONTEXT_EVAL") == APPROVAL:
        prepare_runtime_clients(monkeypatch)
    install_dispatch_guard(monkeypatch, budget)
    decider = ConversationModel()
    generator = ModelClient(bedrock=BedrockProvider())
    report = {
        **assets(),
        "mode": "real_model_synthetic_providers",
        "budget": {
            "calls": budget.max_calls,
            "calls_per_scenario": budget.max_scenario_calls,
            "input_tokens_per_call": budget.max_input,
            "output_tokens_per_call": budget.max_output,
            "seconds_to_start_last_call": budget.max_seconds,
            "maximum_usd_before_tax": 0.8118,
        },
        "model_id": APPROVED_MODEL,
        "region": APPROVED_REGION,
        "scenarios": [],
        "calls": budget.calls,
        "preflights": budget.preflights,
        "google_calendar_calls": google_calls,
        "human_review_required": True,
    }
    destination = Path(os.environ["THREADLY_CONTEXT_EVAL_REPORT"])
    try:
        for scenario in scenarios():
            if budget.halted:
                report["stopped"] = "preflight_or_provider_failure"
                break
            budget.scenario = scenario["id"]
            record = {"id": scenario["id"], "rubric": scenario["rubric"], "turns": []}
            report["scenarios"].append(record)
            seeded = await archived_chat(db_sessionmaker, scenario.get("seed", []))
            chat_id, version = seeded.request.conversation_id, len(scenario.get("seed", []))
            for index, text in enumerate(scenario["turns"]):
                if (
                    budget.halted
                    or len(budget.calls) >= budget.max_calls
                    or sum(c["scenario"] == budget.scenario for c in budget.calls)
                    >= budget.max_scenario_calls
                ):
                    record["stopped"] = "call_budget"
                    break
                item = {"user": text}
                record["turns"].append(item)
                context = {}
                try:
                    async with source_data.source_scope():
                        if index == 0 and scenario.get("pin"):
                            await source_data.fetch(1, TID)
                            async with db_sessionmaker.begin() as db:
                                captured = await source_data.capture(db, 1, TID, message_id=MID)
                            context["context_snapshot_id"] = captured.id
                        result = await service.turn(
                            1,
                            turn(
                                text, conversation_id=chat_id, expected_version=version, **context
                            ),
                            factory=db_sessionmaker,
                            model=decider,
                        )
                    version = result["version"]
                    item["response"] = result
                    if result.get("task_id"):
                        await worker.run_once(db_sessionmaker, generator)
                except ApiError as exc:
                    item["error"] = {"code": exc.code, "status": exc.status}
                    break  # Leave failure in evidence; do not invent a successful continuation.
                async with db_sessionmaker() as db:
                    state = store.decode(await db.get(Conversation, chat_id))
                    item["state"] = {
                        k: state.get(k)
                        for k in (
                            "active_goal_id",
                            "calendar_event_request",
                            "email_draft_goal",
                            "active_task_id",
                        )
                    }
                    seeded.state = state
                    item["goals"] = await goals.listing(seeded)
                    if result.get("task_id"):
                        task = await db.get(AssistantTask, result["task_id"])
                        item["task"] = {
                            "state": task.state,
                            "error_code": task.error_code,
                            "artifact_id": task.final_artifact_id,
                        }
                        if task.final_artifact_id:
                            artifact = await db.get(ArtifactRevision, task.final_artifact_id)
                            item["task"]["artifact"] = artifact.payload
            async with db_sessionmaker() as db:
                assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AssistantAction)
                        .where(AssistantAction.state.in_(["approved", "running", "succeeded"]))
                    )
                    == 0
                )
    finally:
        destination.write_text(json.dumps(report, indent=2, default=str) + "\n")
        budget.close()
        get_settings.cache_clear()
