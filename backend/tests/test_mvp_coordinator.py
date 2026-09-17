"""Reviewed whole-command routing with deterministic provider fixtures."""

# ruff: noqa: F811
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.assistant import command_plans, coordinator, worker
from app.db.models import AssistantJob, AssistantTask
from app.model_client.client import GenResult
from app.schemas.coordinator import CoordinatorRequest
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_mvp_workflows import Model as ExecutionModel


def parsed(operations, text="summarise schedule tomorrow reply", *, prohibited=None):
    clauses = [
        {"start": 1, "end": len(text.split()), "kind": "requested", "operations": operations}
    ]
    if prohibited:
        clauses[0]["end"] -= 1
        clauses += [
            {
                "start": len(text.split()),
                "end": len(text.split()),
                "kind": "prohibited",
                "operations": prohibited,
            }
        ]
    return {
        "command": {
            "clauses": clauses,
            "summary_usage": "separate"
            if "summary" in operations and set(operations) & {"reply", "compose"}
            else "not_applicable",
            "lookup_query": None,
            "ambiguities": [],
        },
        "scheduling": {
            "clauses": clauses,
            "operation": "suggest_slots",
            "date": {"start": 3, "end": 3},
            "time": None,
            "duration": None,
            "count": None,
            "timezone": None,
            "daypart": None,
            "unhandled": [],
        }
        if "schedule" in operations
        else None,
        "other_operation": "help" if "other" in operations else None,
    }


class Model:
    def __init__(self, output):
        self.output = output

    async def generate(self, *args, **kwargs):
        return json.dumps(self.output), GenResult("fake", "master-fixture")


@needs_pg
async def test_master_review_to_complete_graph(db_sessionmaker, setup, db_client, auth_headers):
    req = CoordinatorRequest(
        schema_version="1.0",
        request_id="master",
        instruction="summarise schedule tomorrow compose",
        context_snapshot_id=capture(db_client, auth_headers),
        draft_options={"to": ["person@example.test"]},
        expected_preferences_version=1,
    )
    async with db_sessionmaker.begin() as session:
        row, created = await coordinator.reserve(session, 1, req)
    assert created
    state, result = await coordinator.interpret(
        row, Model(parsed(["summary", "schedule", "compose"], req.instruction))
    )
    assert state == "proposed", result
    assert result["compiled_request"]["operations"] == ["summary", "schedule", "draft_new"]
    async with db_sessionmaker.begin() as session:
        row = await command_plans.complete(session, 1, row.id, state, result)
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0
    confirmation = {"plan_hash": row.plan_hash, "confirm_complete_command": True}
    url = f"/assistant/workflow-proposals/{row.id}/confirm"
    assert db_client.post(url, headers=auth_headers(2), json=confirmation).status_code == 404
    assert (
        db_client.post(
            url, headers=auth_headers(1), json={**confirmation, "plan_hash": "0" * 64}
        ).status_code
        == 409
    )
    result = db_client.post(url, headers=auth_headers(1), json=confirmation)
    assert result.status_code == 202, result.text
    task_id = result.json()["task_id"]
    assert (
        db_client.post(url, headers=auth_headers(1), json=confirmation).json()["task_id"] == task_id
    )
    await worker.run_once(db_sessionmaker, ExecutionModel())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "succeeded", task.error_code


@pytest.mark.parametrize(
    "operations,state",
    [
        (["summary"], "proposed"),
        (["plan"], "proposed"),
        (["other"], "proposed"),
        (["summary", "send"], "unsupported"),
        (["summary", "book"], "unsupported"),
        (["reply", "compose"], "unsupported"),
        (["search_mailbox"], "unsupported"),
        (["summary", "summary"], "invalid"),
    ],
)
def test_complete_operation_matrix(operations, state):
    request = CoordinatorRequest(
        schema_version="1.0",
        request_id="x",
        instruction="do the complete request",
        context_snapshot_id="context",
    )
    row = SimpleNamespace(id="p", request=request.model_dump(), release={"binding": None})
    data = parsed(operations, request.instruction)
    if state == "invalid":
        with pytest.raises(ValueError):
            coordinator.parse(json.dumps(data), request.instruction)
    else:
        proposal = coordinator.parse(json.dumps(data), request.instruction)
        actual, result = coordinator.compile_request(row, proposal)
        assert actual == state, result


def test_negation_and_missing_coverage():
    text = "summarise do not summarise"
    row = SimpleNamespace(
        id="p",
        release={},
        request=CoordinatorRequest(
            schema_version="1.0", request_id="x", instruction=text, context_snapshot_id="context"
        ).model_dump(),
    )
    data = parsed(["summary"], text, prohibited=["summary"])
    proposal = coordinator.parse(json.dumps(data), text)
    assert coordinator.compile_request(row, proposal)[0] == "needs_clarification"
    data["command"]["clauses"][0]["start"] = 2
    with pytest.raises(ValueError):
        coordinator.parse(json.dumps(data), text)


@needs_pg
@pytest.mark.parametrize("operation", ["compose", "schedule"])
async def test_standalone_commands_do_not_require_email_context(
    db_sessionmaker, setup, db_client, auth_headers, operation
):
    from tests.test_draft_workflows import DraftModel

    instruction = "find times tomorrow" if operation == "schedule" else "compose an email"
    req = CoordinatorRequest(
        schema_version="1.0",
        request_id="standalone",
        instruction=instruction,
        context_snapshot_id=None,
        expected_preferences_version=1 if operation == "schedule" else None,
        draft_options={"to": ["recipient@example.test"]} if operation == "compose" else None,
    )
    async with db_sessionmaker.begin() as session:
        row, _ = await coordinator.reserve(session, 1, req)
    state, result = await coordinator.interpret(row, Model(parsed([operation], instruction)))
    assert state == "proposed", result
    assert result["compiled_request"]["context_snapshot_id"] is None
    async with db_sessionmaker.begin() as session:
        row = await command_plans.complete(session, 1, row.id, state, result)
    response = db_client.post(
        f"/assistant/workflow-proposals/{row.id}/confirm",
        headers=auth_headers(1),
        json={"plan_hash": row.plan_hash, "confirm_complete_command": True},
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]

    class ComposePipeline(DraftModel):
        async def generate(self, prompt, **kwargs):
            if kwargs.get("small"):
                from tests.test_intent_router import proposal

                return json.dumps(
                    proposal(intent="compose", output_kind="draft", operations=["draft_new"])
                ), GenResult("fake", "router")
            return await super().generate(prompt, **kwargs)

    await worker.run_once(db_sessionmaker, ComposePipeline())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "succeeded", task.error_code
        if operation == "schedule":
            assert task.scheduling_input["anchor_at"] == row.release["binding"]["anchor_at"]
