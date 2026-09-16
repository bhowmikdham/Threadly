"""Reviewed command planning: synthetic interpretation, real database and existing executor."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.api.errors import ApiError
from app.assistant import command_plans
from app.assistant.worker import run_once
from app.db.models import AssistantJob, AssistantTask, CommandPlan, Thread
from app.model_client.client import GenResult
from app.model_client.providers import ProviderError
from app.planner import command
from app.schemas.command_plan import CommandPlanRequest, ConfirmCommandPlan
from tests.conftest import needs_pg
from tests.test_compound_workflows import PairModel, capture
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]
CASES = json.loads((Path(__file__).parent / "fixtures/command_plans_v1.json").read_text())


def proposal_for(case):
    words, clauses = 0, []
    for segment in case["segments"]:
        count = len(segment["text"].split())
        clauses.append(
            {
                "start": words + 1,
                "end": words + count,
                "kind": segment["kind"],
                "operations": segment["operations"],
            }
        )
        words += count
    instruction = " ".join(s["text"] for s in case["segments"])
    return instruction, {
        "clauses": clauses,
        "summary_usage": case["summary_usage"],
        "lookup_query": case.get("lookup_query"),
        "ambiguities": case.get("ambiguities", []),
    }


def request(instruction, context="context", reply=False, **changes):
    return CommandPlanRequest.model_validate(
        {
            "schema_version": "1.0",
            "request_id": "command-1",
            "instruction": instruction,
            "context_snapshot_id": context,
            "draft_options": {
                "to": ["recipient@example.test"],
                "bcc": ["private@example.test"],
                "reply_message_id": "m1" if reply else None,
            },
            **changes,
        }
    )


def test_pinned_contract():
    assert CASES["release"] == command.RELEASE
    assert CASES["contract_hash"] == command.contract_hash()
    assert CASES["live_model_evaluated"] is False


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_command_coverage_dependency_and_rejection_corpus(case):
    instruction, output = proposal_for(case)
    parsed = command.parse(json.dumps(output), instruction)
    state, result = command.compile_plan(
        request(instruction, reply=case.get("reply", False)), parsed, "plan"
    )
    assert state == case["state"]
    assert [c["text"] for c in result["clauses"]] == [s["text"] for s in case["segments"]]
    if state == "proposed":
        assert result["compiled_request"]["template"] == case["template"]
        if "summary" in case["template"]:
            assert result["compiled_request"]["summary_in_draft"] == (
                case["summary_usage"] == "include"
            )
        else:
            assert result["compiled_request"]["query"] == "Friday"
        assert "Do not send" in result["compiled_request"]["draft_instruction"]
    else:
        assert result["compiled_request"] is None


@pytest.mark.parametrize(
    "mutation", ["gap", "overlap", "tail", "unknown", "extra", "query", "fence", "duplicate_key"]
)
def test_invalid_model_contract(mutation):
    instruction, value = proposal_for(CASES["cases"][0])
    if mutation == "gap":
        value["clauses"][0]["start"] = 2
    elif mutation == "overlap":
        value["clauses"][1]["start"] -= 1
    elif mutation == "tail":
        value["clauses"].pop()
    elif mutation == "unknown":
        value["clauses"][0]["operations"] = ["arbitrary_tool"]
    elif mutation == "extra":
        value["flow_arn"] = "untrusted"
    elif mutation == "query":
        value["lookup_query"] = {"start": 1, "end": 1}
    text = json.dumps(value)
    if mutation == "fence":
        text = "```json\n" + text + "\n```"
    if mutation == "duplicate_key":
        text = text[:-1] + ',"summary_usage":"separate"}'
    with pytest.raises(ValueError):
        command.parse(text, instruction)


class PlannerModel:
    def __init__(self, output, error=None):
        self.output, self.error, self.calls = output, error, []

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return json.dumps(self.output), GenResult("fake", "command-plan-fixture")


async def prepared(factory, owner, case=None, **changes):
    case = case or CASES["cases"][0]
    context = await capture(factory, owner)
    instruction, output = proposal_for(case)
    req = request(instruction, context, reply=case.get("reply", False), **changes)
    async with factory.begin() as session:
        plan, created = await command_plans.reserve(session, owner, req)
    assert created
    state, result = await command_plans.interpret(plan, PlannerModel(output))
    async with factory.begin() as session:
        return await command_plans.complete(session, owner, plan.id, state, result)


def confirmation(plan):
    return ConfirmCommandPlan(plan_hash=plan.plan_hash, confirm_complete_command=True)


@needs_pg
async def test_api_review_before_execution_and_idempotent_confirmation(
    db_sessionmaker, mailbox, db_client, auth_headers, monkeypatch
):
    owner = mailbox[0]
    context = await capture(db_sessionmaker, owner)
    instruction, output = proposal_for(CASES["cases"][0])
    model = PlannerModel(output)
    monkeypatch.setattr(command_plans, "get_model_client", lambda: model)
    req = request(instruction, context)
    headers = auth_headers(owner)
    response = db_client.post("/assistant/command-plans", headers=headers, json=req.model_dump())
    assert response.status_code == 202, response.text
    plan = response.json()
    assert plan["state"] == "proposed" and plan["task_id"] is None
    assert plan["requires_complete_command_review"]
    assert "private@example.test" not in model.calls[0] and context not in model.calls[0]
    assert "Ship Friday" not in model.calls[0]  # source email never enters the planner
    assert (
        db_client.post("/assistant/command-plans", headers=headers, json=req.model_dump()).json()[
            "plan_id"
        ]
        == plan["plan_id"]
    )
    assert len(model.calls) == 1
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0
    path = f"/assistant/command-plans/{plan['plan_id']}/confirm"
    assert (
        db_client.post(
            path,
            headers=headers,
            json={"plan_hash": plan["plan_hash"], "confirm_complete_command": False},
        ).status_code
        == 422
    )
    assert (
        db_client.post(
            path, headers=headers, json={"plan_hash": "0" * 64, "confirm_complete_command": True}
        ).status_code
        == 409
    )
    body = {"plan_hash": plan["plan_hash"], "confirm_complete_command": True}
    first = db_client.post(path, headers=headers, json=body)
    assert first.status_code == 202, first.text
    assert first.json()["compound"]["template"] == "summary_then_compose"
    assert (
        db_client.post(path, headers=headers, json=body).json()["task_id"]
        == first.json()["task_id"]
    )
    await run_once(db_sessionmaker, PairModel())
    task = db_client.get(f"/assistant/tasks/{first.json()['task_id']}", headers=headers).json()
    assert task["state"] == "succeeded"
    stored = db_client.get(f"/assistant/command-plans/{plan['plan_id']}", headers=headers).json()
    assert stored["state"] == "consumed" and stored["task_id"] == task["task_id"]
    assert (
        db_client.get(
            f"/assistant/command-plans/{plan['plan_id']}", headers=auth_headers(mailbox[1])
        ).status_code
        == 404
    )
    assert db_client.post(path, headers=auth_headers(mailbox[1]), json=body).status_code == 404


@needs_pg
async def test_all_installed_pairs_execute_from_confirmed_plan(db_sessionmaker, mailbox):
    for n, case in enumerate(CASES["cases"]):
        if case["state"] != "proposed":
            continue
        plan = await prepared(db_sessionmaker, mailbox[0], case, request_id=f"case-{n}")
        async with db_sessionmaker.begin() as session:
            task = await command_plans.confirm(session, mailbox[0], plan.id, confirmation(plan))
        await run_once(db_sessionmaker, PairModel())
        async with db_sessionmaker() as session:
            assert (await session.get(AssistantTask, task.id)).state == "succeeded"


@pytest.mark.parametrize(
    "case", [c for c in CASES["cases"] if c["state"] != "proposed"], ids=lambda c: c["id"]
)
@needs_pg
async def test_unsupported_or_ambiguous_whole_plan_creates_zero_jobs(
    db_sessionmaker, mailbox, case
):
    plan = await prepared(db_sessionmaker, mailbox[0], case)
    assert plan.state == case["state"]
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await command_plans.confirm(session, mailbox[0], plan.id, confirmation(plan))
    assert exc.value.code == "plan_not_confirmable"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0


@needs_pg
async def test_missing_bindings_collected_without_guessing(db_sessionmaker, mailbox):
    instruction, output = proposal_for(CASES["cases"][1])
    req = request(instruction, context=None, draft_options=None)
    async with db_sessionmaker.begin() as session:
        plan, _ = await command_plans.reserve(session, mailbox[0], req)
    state, result = await command_plans.interpret(plan, PlannerModel(output))
    assert state == "needs_clarification"
    assert result["missing_fields"] == ["source_context", "recipients", "reply_target"]


@pytest.mark.parametrize("change", ["source", "expiry", "release", "result"])
@needs_pg
async def test_confirm_revalidates_saved_inputs(db_sessionmaker, mailbox, change, monkeypatch):
    plan = await prepared(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        if change == "source":
            await session.execute(update(Thread).values(version=Thread.version + 1))
        elif change == "expiry":
            await session.execute(
                update(CommandPlan).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        elif change == "result":
            row = await session.get(CommandPlan, plan.id)
            result = copy.deepcopy(row.result)
            result["compiled_request"]["summary_in_draft"] = False
            row.result = result
        else:
            monkeypatch.setattr(command_plans, "execution_release", lambda *args: {"changed": True})
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await command_plans.confirm(session, mailbox[0], plan.id, confirmation(plan))
    assert (
        exc.value.code
        == {
            "source": "read_source_changed",
            "expiry": "plan_not_confirmable",
            "release": "release_unavailable",
            "result": "plan_changed",
        }[change]
    )
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0


@needs_pg
async def test_competing_confirmations_publish_one_task_and_caller_rollback(
    db_sessionmaker, mailbox
):
    plan = await prepared(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        await command_plans.confirm(session, mailbox[0], plan.id, confirmation(plan))
        await session.rollback()
    async with db_sessionmaker() as session:
        assert (await session.get(CommandPlan, plan.id)).state == "proposed"
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0

    async def confirm():
        async with db_sessionmaker.begin() as session:
            return (
                await command_plans.confirm(session, mailbox[0], plan.id, confirmation(plan))
            ).id

    ids = await asyncio.gather(confirm(), confirm())
    assert ids[0] == ids[1]
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 1


@needs_pg
async def test_duplicate_reservation_and_crash_expiry(db_sessionmaker, mailbox):
    context = await capture(db_sessionmaker, mailbox[0])
    instruction, _ = proposal_for(CASES["cases"][0])
    req = request(instruction, context)

    async def reserve():
        async with db_sessionmaker.begin() as session:
            p, fresh = await command_plans.reserve(session, mailbox[0], req)
            return p.id, fresh

    reservations = await asyncio.gather(reserve(), reserve())
    assert reservations[0][0] == reservations[1][0] and sum(f for _, f in reservations) == 1
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(CommandPlan).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        row = await session.get(CommandPlan, reservations[0][0])
        assert (await command_plans.view(session, row))["state"] == "expired"
        late = await command_plans.complete(
            session, mailbox[0], row.id, "failed", {"reason": "late"}
        )
        assert late.state == "expired" and late.result is None
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await command_plans.reserve(
                session, mailbox[0], req.model_copy(update={"instruction": "changed"})
            )
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize("bad", ["invalid", "provider", "unexpected"])
@needs_pg
async def test_interpret_failure_sanitized_no_generation(db_sessionmaker, mailbox, bad):
    instruction, output = proposal_for(CASES["cases"][0])
    context = await capture(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        plan, _ = await command_plans.reserve(session, mailbox[0], request(instruction, context))
    error = (
        ProviderError("PRIVATE_ERROR")
        if bad == "provider"
        else RuntimeError("PRIVATE_ERROR")
        if bad == "unexpected"
        else None
    )
    model = PlannerModel({"private": "INVALID_PRIVATE"} if bad == "invalid" else output, error)
    state, result = await command_plans.interpret(plan, model)
    assert state == "failed" and "PRIVATE" not in json.dumps(result)
    async with db_sessionmaker.begin() as session:
        await command_plans.complete(session, mailbox[0], plan.id, state, result)
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0


@needs_pg
async def test_source_owner_checked_before_inference_and_reservation(db_sessionmaker, mailbox):
    context = await capture(db_sessionmaker, mailbox[0])
    instruction, _ = proposal_for(CASES["cases"][0])
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await command_plans.reserve(session, mailbox[1], request(instruction, context))
    assert exc.value.code == "context_not_found"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(CommandPlan)) == 0


@needs_pg
async def test_model_runs_without_reservation_transaction(db_sessionmaker, mailbox):
    context = await capture(db_sessionmaker, mailbox[0])
    instruction, output = proposal_for(CASES["cases"][0])
    async with db_sessionmaker.begin() as session:
        plan, _ = await command_plans.reserve(session, mailbox[0], request(instruction, context))

    class CheckLocks(PlannerModel):
        async def generate(self, prompt, **kwargs):
            async with db_sessionmaker.begin() as session:
                row = await session.scalar(
                    select(CommandPlan)
                    .where(CommandPlan.id == plan.id)
                    .with_for_update(nowait=True)
                )
                assert row.state == "planning"
                assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0
            return await super().generate(prompt, **kwargs)

    state, _ = await command_plans.interpret(plan, CheckLocks(output))
    assert state == "proposed"


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_confirmation_is_explicit_strict_boolean(value):
    with pytest.raises(ValueError):
        ConfirmCommandPlan(plan_hash="a" * 64, confirm_complete_command=value)
