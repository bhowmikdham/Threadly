"""Synthetic model outputs, real PostgreSQL fences and existing fake Google executor."""
# ruff: noqa: F811

import asyncio
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.api.errors import ApiError
from app.assistant import scheduling_proposals as service
from app.assistant.worker import run_once
from app.db.models import (
    AssistantJob,
    AssistantTask,
    CalendarPreference,
    SchedulingProposal,
    Thread,
    User,
)
from app.planner import scheduling_extraction as extraction
from app.schemas.scheduling_proposal import ConfirmSchedulingProposal, SchedulingProposalRequest
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_command_plans import PlannerModel

CASES = json.loads((Path(__file__).parent / "fixtures/scheduling_extraction_v1.json").read_text())
BINDING = {
    "anchor_at": "2026-09-17T02:00:00+00:00",
    "anchor_source": "request_received",
    "preferences": {"timezone": "UTC", "default_duration_minutes": 30},
}


def request(case=None, **changes):
    return SchedulingProposalRequest.model_validate(
        {
            "schema_version": "1.0",
            "request_id": "p1",
            "instruction": (case or CASES["cases"][0])["instruction"],
            "expected_preferences_version": 1,
            **changes,
        }
    )


def confirmation(plan):
    return ConfirmSchedulingProposal(proposal_hash=plan.plan_hash, confirm_complete_request=True)


def test_pinned_contract():
    assert CASES["release"] == extraction.RELEASE
    assert CASES["contract_hash"] == extraction.contract_hash()
    assert CASES["live_model_evaluated"] is False


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_replay_corpus(case):
    parsed = extraction.parse(json.dumps(case["output"]), case["instruction"])
    state, result = extraction.compile_proposal(request(case), parsed, "p", BINDING)
    assert state == case["state"]
    assert result["calendar_checked"] is False
    if state == "proposed":
        constraints = result["compiled_request"]["constraints"]
        for key, value in case["constraints"].items():
            assert constraints[key] == value
    else:
        assert result["compiled_request"] is None


@pytest.mark.parametrize(
    "bad", ["gap", "tail", "overlap", "span", "extra", "unknown", "duplicate", "fence"]
)
def test_invalid_model_output_rejected(bad):
    case = CASES["cases"][0]
    value = copy.deepcopy(case["output"])
    if bad == "gap":
        value["clauses"][0]["start"] = 2
    if bad == "tail":
        value["clauses"][0]["end"] -= 1
    if bad == "overlap":
        value["time"] = value["date"]
    if bad == "span":
        value["time"] = {"start": 1, "end": 500}
    if bad == "extra":
        value["slot_id"] = "invented"
    if bad == "unknown":
        value["operation"] = "send_email"
    text = json.dumps(value)
    if bad == "duplicate":
        text = text[:-1] + ',"date":null}'
    if bad == "fence":
        text = "```json\n" + text + "\n```"
    with pytest.raises(ValueError):
        extraction.parse(text, case["instruction"])


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_confirmation_strict(value):
    with pytest.raises(ValueError):
        ConfirmSchedulingProposal(proposal_hash="a" * 64, confirm_complete_request=value)


async def prepared(factory, case=None, **changes):
    case = case or CASES["cases"][2]
    async with factory.begin() as session:
        plan, created = await service.reserve(session, 1, request(case, **changes))
    assert created
    state, result = await service.interpret(plan, PlannerModel(case["output"]))
    async with factory.begin() as session:
        return await service.complete(session, 1, plan.id, state, result)


@needs_pg
async def test_api_review_execution_and_replay(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    case = CASES["cases"][2]
    model = PlannerModel(case["output"])
    monkeypatch.setattr(service, "get_model_client", lambda: model)
    headers = auth_headers(1)
    response = db_client.post(
        "/assistant/scheduling-proposals", headers=headers, json=request(case).model_dump()
    )
    assert response.status_code == 202, response.text
    proposal = response.json()
    assert proposal["state"] == "proposed" and proposal["task_id"] is None
    assert len(model.calls) == 1 and not setup.calls
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0
    assert (
        db_client.post(
            "/assistant/scheduling-proposals", headers=headers, json=request(case).model_dump()
        ).json()["proposal_id"]
        == proposal["proposal_id"]
    )
    assert len(model.calls) == 1
    path = f"/assistant/scheduling-proposals/{proposal['proposal_id']}"
    assert db_client.get(path, headers=auth_headers(2)).status_code == 404
    body = dict(proposal_hash=proposal["proposal_hash"], confirm_complete_request=True)
    assert db_client.post(path + "/confirm", headers=auth_headers(2), json=body).status_code == 404
    assert (
        db_client.post(
            path + "/confirm", headers=headers, json={**body, "proposal_hash": "0" * 64}
        ).status_code
        == 409
    )
    response = db_client.post(path + "/confirm", headers=headers, json=body)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["scheduling"]["anchor_at"] == proposal["result"]["anchor_at"]
    assert (
        db_client.post(path + "/confirm", headers=headers, json=body).json()["task_id"]
        == task["task_id"]
    )
    assert await run_once(db_sessionmaker)
    result = db_client.get("/assistant/tasks/" + task["task_id"], headers=headers).json()
    assert result["state"] == "succeeded", result
    assert db_client.get(path, headers=headers).json()["state"] == "consumed"
    assert setup.calls  # Only confirmed task reads Google.


@needs_pg
async def test_concurrent_confirmation_and_rollback(db_sessionmaker, setup):
    proposal = await prepared(db_sessionmaker)
    async with db_sessionmaker() as session:
        await service.confirm(session, 1, proposal.id, confirmation(proposal))
        await session.rollback()
    async with db_sessionmaker() as session:
        assert (await session.get(SchedulingProposal, proposal.id)).state == "proposed"
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0

    async def confirm():
        async with db_sessionmaker.begin() as session:
            return (await service.confirm(session, 1, proposal.id, confirmation(proposal))).id

    result = await asyncio.gather(confirm(), confirm())
    assert result[0] == result[1]
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 1


@pytest.mark.parametrize("change", ["prefs", "account", "expiry", "result", "release", "source"])
@needs_pg
async def test_stale_proposal_cannot_dispatch(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch, change
):
    context = capture(db_client, auth_headers)
    proposal = await prepared(db_sessionmaker, context_snapshot_id=context)
    async with db_sessionmaker.begin() as session:
        if change == "prefs":
            await session.execute(update(CalendarPreference).values(version=2))
        elif change == "account":
            await session.execute(update(User).values(google_account_version=2))
        elif change == "source":
            await session.execute(update(Thread).values(version=2))
        elif change == "expiry":
            await session.execute(
                update(SchedulingProposal).values(expires_at=func.clock_timestamp())
            )
        elif change == "result":
            row = await session.get(SchedulingProposal, proposal.id)
            value = copy.deepcopy(row.result)
            value["compiled_request"]["constraints"]["count"] = 1
            row.result = value
        else:
            monkeypatch.setattr(extraction, "contract_hash", lambda: "changed")
    with pytest.raises(ApiError):
        async with db_sessionmaker.begin() as session:
            await service.confirm(session, 1, proposal.id, confirmation(proposal))
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0


@needs_pg
async def test_reservation_race_and_no_transaction_during_model(db_sessionmaker, setup):
    async def reserve():
        async with db_sessionmaker.begin() as session:
            return await service.reserve(session, 1, request())

    results = await asyncio.gather(reserve(), reserve())
    assert results[0][0].id == results[1][0].id and sum(r[1] for r in results) == 1
    plan = results[0][0]

    class CheckLocks(PlannerModel):
        async def generate(self, prompt, **kwargs):
            async with db_sessionmaker.begin() as session:
                await session.scalar(select(User).where(User.id == 1).with_for_update(nowait=True))
                await session.scalar(
                    select(SchedulingProposal)
                    .where(SchedulingProposal.id == plan.id)
                    .with_for_update(nowait=True)
                )
            return await super().generate(prompt, **kwargs)

    assert (await service.interpret(plan, CheckLocks(CASES["cases"][0]["output"])))[0] == "proposed"
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await service.reserve(session, 1, request(instruction="different"))
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize("kind", ["malformed", "exception", "unsupported", "clarification"])
@needs_pg
async def test_failed_or_incomplete_proposal_never_queues(db_sessionmaker, setup, kind):
    case = CASES["cases"][7 if kind == "unsupported" else 10 if kind == "clarification" else 0]
    async with db_sessionmaker.begin() as session:
        proposal, _ = await service.reserve(session, 1, request(case))
    model = PlannerModel(
        {} if kind == "malformed" else case["output"],
        RuntimeError("PRIVATE") if kind == "exception" else None,
    )
    state, result = await service.interpret(proposal, model)
    assert state in {"failed", "unsupported", "needs_clarification"}
    assert "PRIVATE" not in json.dumps(result)
    async with db_sessionmaker.begin() as session:
        proposal = await service.complete(session, 1, proposal.id, state, result)
    with pytest.raises(ApiError):
        async with db_sessionmaker.begin() as session:
            await service.confirm(session, 1, proposal.id, confirmation(proposal))
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0


@needs_pg
async def test_fixed_message_anchor_no_source_sent_to_model(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    async with db_sessionmaker.begin() as session:
        proposal, _ = await service.reserve(
            session, 1, request(context_snapshot_id=context, anchor_message_id="schedule-message")
        )
    model = PlannerModel(CASES["cases"][0]["output"])
    state, result = await service.interpret(proposal, model)
    assert context not in model.calls[0] and "Are you free tomorrow at four?" not in model.calls[0]
    assert result["anchor_source"] == "source_message"
    async with db_sessionmaker.begin() as session:
        proposal = await service.complete(session, 1, proposal.id, state, result)
    async with db_sessionmaker.begin() as session:
        task = await service.confirm(session, 1, proposal.id, confirmation(proposal))
    assert task.scheduling_input["anchor_at"] == proposal.binding["anchor_at"]
    assert task.scheduling_input["anchor_source"] == "source_message"


@needs_pg
async def test_owner_rejected_before_model_and_expired_crash_receipt(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await service.reserve(session, 2, request(context_snapshot_id=context))
    assert exc.value.code == "context_not_found"
    async with db_sessionmaker.begin() as session:
        proposal, _ = await service.reserve(session, 1, request())
    async with db_sessionmaker.begin() as session:
        await session.execute(update(SchedulingProposal).values(expires_at=func.clock_timestamp()))
    async with db_sessionmaker.begin() as session:
        proposal, created = await service.reserve(session, 1, request())
        assert not created
        assert (await service.view(session, proposal))["state"] == "expired"
        late = await service.complete(session, 1, proposal.id, "failed", {"reason": "late"})
        assert late.state == "expired" and late.result is None


def test_relative_dates_resolve_in_explicit_zone_at_saved_anchor():
    case = CASES["cases"][3]
    parsed = extraction.parse(json.dumps(case["output"]), case["instruction"])
    binding = {**BINDING, "anchor_at": "2026-09-17T23:59:00+00:00"}
    state, result = extraction.compile_proposal(request(case), parsed, "p", binding)
    assert state == "proposed"
    assert result["compiled_request"]["constraints"]["date"] == "2026-09-19"
    assert result["anchor_at"] == binding["anchor_at"]
    assert result["assumptions"][0] == {
        "field": "timezone",
        "value": "Australia/Melbourne",
        "source": "command",
    }
