"""MVP graphs: real PostgreSQL and fake model/Google transports."""

# ruff: noqa: F811
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.assistant import planning, tasks, worker, workflows
from app.db.models import (
    ArtifactRevision,
    AssistantJob,
    AssistantStep,
    AssistantTask,
    CalendarSlotRequest,
    Thread,
)
from app.model_client.client import GenResult
from app.schemas.workflow import WorkflowRequest
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401


class Model:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self.callback:
            await self.callback()
        if "action plan" in prompt:
            data = {
                "items": [
                    {
                        "text": "Discuss availability",
                        "owner": None,
                        "due_date": None,
                        "sources": [1],
                        "quote": "Are you free tomorrow at four?",
                        "depends_on": [],
                    }
                ],
                "open_questions": [],
            }
        else:
            data = {
                "overview": "A meeting time is requested.",
                "decisions": [],
                "actions": [],
                "open_questions": [],
            }
        return json.dumps(data), GenResult("fake", "mvp-fixture")


def body(context, **changes):
    return {
        "schema_version": "1.0",
        "request_id": "workflow-1",
        "context_snapshot_id": context,
        "instruction": "Summarise this and suggest three times in a draft.",
        "operations": ["summary", "schedule", "draft_new"],
        "summary_in_draft": True,
        "schedule": {
            "operation": "suggest_slots",
            "expected_preferences_version": 1,
            "constraints": {"date": "tomorrow"},
        },
        "draft_options": {"to": ["recipient@example.test"]},
        **changes,
    }


@needs_pg
async def test_complete_graph_distinct_outputs_replay_and_expiry(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    request = body(context)
    response = db_client.post("/assistant/workflow-requests", headers=auth_headers(1), json=request)
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]
    assert (
        db_client.post(
            "/assistant/workflow-requests", headers=auth_headers(1), json=request
        ).json()["task_id"]
        == task_id
    )
    model = Model()
    assert await worker.run_once(db_sessionmaker, model)
    result = db_client.get("/assistant/tasks/" + task_id, headers=auth_headers(1)).json()
    assert result["state"] == "succeeded", result
    assert result["workflow"]["completed_steps"] == 3 and len(model.calls) == 1
    async with db_sessionmaker() as session:
        artifacts = (
            await session.scalars(
                select(ArtifactRevision).where(ArtifactRevision.task_id == task_id)
            )
        ).all()
        assert {a.stream_key for a in artifacts} == {"summary", "schedule", "result"}
        draft = next(a for a in artifacts if a.stream_key == "result")
        schedule = next(a for a in artifacts if a.stream_key == "schedule")
        assert draft.id == result["artifact_id"]
        assert draft.payload["calendar_grounding"]["slot_ids"] == [
            s["id"] for s in schedule.payload["content"]["slots"]
        ]
        for slot in schedule.payload["content"]["slots"]:
            assert slot["start_local"] in draft.payload["content"]["body"]
        assert "A meeting time is requested." in draft.payload["content"]["body"]
    response = db_client.get("/assistant/artifacts/" + draft.id, headers=auth_headers(1))
    assert response.status_code == 200, response.text
    assert not response.json()["review"]["blockers"], response.text
    async with db_sessionmaker.begin() as session:
        await session.execute(update(CalendarSlotRequest).values(expires_at=func.clock_timestamp()))
    result = db_client.get("/assistant/artifacts/" + draft.id, headers=auth_headers(1)).json()
    assert "calendar_grounding_expired" in result["review"]["blockers"]


@needs_pg
async def test_unknown_calendar_cannot_publish_draft(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    setup.unknown = True
    response = db_client.post(
        "/assistant/workflow-requests", headers=auth_headers(1), json=body(context)
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]
    await worker.run_once(db_sessionmaker, Model())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task_id)
        assert task.state == "failed" and task.error_code == "calendar_coverage_unknown", (
            task.error_code
        )
        assert task.final_artifact_id is None
        artifacts = (
            await session.scalars(
                select(ArtifactRevision).where(ArtifactRevision.task_id == task_id)
            )
        ).all()
        assert [a.payload["kind"] for a in artifacts] == ["summary"]


@pytest.mark.parametrize(
    "change",
    [
        {"operations": ["summary", "draft_new", "schedule"]},
        {
            "schedule": {
                "operation": "check_time",
                "expected_preferences_version": 1,
                "constraints": {"date": "tomorrow", "at_time": "4"},
            }
        },
        {"draft_options": {"to": []}},
        {
            "schedule": {
                "operation": "suggest_slots",
                "expected_preferences_version": 7,
                "constraints": {"date": "tomorrow"},
            }
        },
    ],
)
@needs_pg
async def test_preflight_has_zero_partial_steps(
    db_sessionmaker, setup, db_client, auth_headers, change
):
    response = db_client.post(
        "/assistant/workflow-requests",
        headers=auth_headers(1),
        json=body(capture(db_client, auth_headers), **change),
    )
    assert response.status_code in {409, 422}, response.text
    assert not setup.calls
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 0


@needs_pg
async def test_changed_source_fences_output(db_sessionmaker, setup, db_client, auth_headers):
    response = db_client.post(
        "/assistant/workflow-requests",
        headers=auth_headers(1),
        json=body(capture(db_client, auth_headers)),
    )

    async def changed():
        async with db_sessionmaker.begin() as session:
            await session.execute(update(Thread).values(version=2))

    await worker.run_once(db_sessionmaker, Model(changed))
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, response.json()["task_id"])
        assert task.state == "failed" and task.final_artifact_id is None
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 0
    assert not setup.calls


@needs_pg
async def test_replay_reuses_summary(db_sessionmaker, setup, db_client, auth_headers, monkeypatch):
    req = WorkflowRequest.model_validate(body(capture(db_client, auth_headers)))
    async with db_sessionmaker.begin() as session:
        task = await tasks.submit(session, 1, req.as_request(), workflow=req)
    async with db_sessionmaker.begin() as session:
        claim = await tasks.claim_next(session)
    original = workflows.slot_draft
    monkeypatch.setattr(
        workflows, "slot_draft", lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    model = Model()
    await workflows.run_task(db_sessionmaker, claim, model)
    async with db_sessionmaker.begin() as session:
        row = await session.get(AssistantTask, task.id)
        row.state = "queued"
        job = await session.get(AssistantJob, task.id)
        job.state = "queued"
    monkeypatch.setattr(workflows, "slot_draft", original)
    assert await worker.run_once(db_sessionmaker, model)
    assert len(model.calls) == 1
    async with db_sessionmaker() as session:
        result = await session.get(AssistantTask, task.id)
        assert result.state == "succeeded", result.error_code
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 3
        assert len((await session.scalars(select(AssistantStep))).all()) == 3


@needs_pg
async def test_plan_proposed_not_committed(db_sessionmaker, setup, db_client, auth_headers):
    request = body(
        capture(db_client, auth_headers),
        operations=["plan"],
        schedule=None,
        draft_options=None,
        summary_in_draft=False,
    )
    response = db_client.post("/assistant/workflow-requests", headers=auth_headers(1), json=request)
    assert response.status_code == 202, response.text
    await worker.run_once(db_sessionmaker, Model())
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, response.json()["task_id"])
        assert task.state == "succeeded", task.error_code
        artifact = await session.get(ArtifactRevision, task.final_artifact_id)
        assert artifact.payload["kind"] == "plan"
        assert artifact.payload["content"]["items"][0]["status"] == "proposed"
        assert artifact.payload["content"]["accepted_item_ids"] == []
    assert not setup.calls


@pytest.mark.parametrize("bad", ["cycle", "source", "quote", "owner", "deadline"])
def test_invalid_plan_rejected(bad):
    claim = SimpleNamespace(
        task_id="p",
        context_id="c",
        snapshot={"messages": [{"message_id": "m1", "body": "Discuss availability."}]},
    )
    item = {
        "text": "Discuss",
        "owner": None,
        "due_date": None,
        "sources": [1],
        "quote": "Discuss availability.",
        "depends_on": [],
    }
    if bad == "cycle":
        item["depends_on"] = [1]
    if bad == "source":
        item["sources"] = [100]
    if bad == "quote":
        item["quote"] = "Invented"
    if bad == "owner":
        item["owner"] = "Alex"
    if bad == "deadline":
        item["due_date"] = "2026-09-31"
    with pytest.raises(ValueError):
        planning.make_artifact(json.dumps({"items": [item], "open_questions": []}), claim)


@needs_pg
async def test_plan_selection_draft_and_invalidation(
    db_sessionmaker, setup, db_client, auth_headers
):
    context = capture(db_client, auth_headers)
    req = body(
        context, operations=["plan"], schedule=None, draft_options=None, summary_in_draft=False
    )
    result = db_client.post("/assistant/workflow-requests", headers=auth_headers(1), json=req)
    task_id = result.json()["task_id"]

    class TwoItemPlan(Model):
        async def generate(self, prompt, **kwargs):
            text, info = await super().generate(prompt, **kwargs)
            data = json.loads(text)
            data["items"].append({**data["items"][0], "text": "Ask about meeting duration"})
            return json.dumps(data), info

    await worker.run_once(db_sessionmaker, TwoItemPlan())
    result = db_client.get("/assistant/tasks/" + task_id, headers=auth_headers(1)).json()
    plan_id = result["artifact_id"]
    artifact = db_client.get("/assistant/artifacts/" + plan_id, headers=auth_headers(1)).json()
    item = artifact["artifact"]["content"]["items"][0]
    draft = body(
        context,
        request_id="plan-draft",
        operations=["draft_new"],
        schedule=None,
        summary_in_draft=False,
        accepted_plan_artifact_id=plan_id,
    )
    assert (
        db_client.post(
            "/assistant/workflow-requests", headers=auth_headers(1), json=draft
        ).status_code
        == 409
    )
    url = f"/assistant/tasks/{task_id}/plan-review"
    accept = {
        "request_id": "accept-plan",
        "expected_revision": 1,
        "accepted_item_ids": [item["id"]],
    }
    result = db_client.post(url, headers=auth_headers(2), json=accept)
    assert result.status_code == 404
    result = db_client.post(
        url, headers=auth_headers(1), json={**accept, "accepted_item_ids": ["foreign"]}
    )
    assert result.status_code == 422
    result = db_client.post(url, headers=auth_headers(1), json=accept)
    assert result.status_code == 200, result.text
    accepted = result.json()
    assert accepted["revision"] == 2
    commitments = db_client.get(
        "/commitments", headers=auth_headers(1), params={"context_snapshot_id": context}
    ).json()
    assert [i["id"] for i in commitments["content"]["items"]] == [item["id"]]
    assert commitments["content"]["items"][0]["completed"] is False
    assert (
        db_client.post(url, headers=auth_headers(1), json=accept).json()["artifact_id"]
        == accepted["artifact_id"]
    )
    assert (
        db_client.post(
            url, headers=auth_headers(1), json={**accept, "request_id": "stale"}
        ).status_code
        == 409
    )
    draft["accepted_plan_artifact_id"] = accepted["artifact_id"]
    result = db_client.post("/assistant/workflow-requests", headers=auth_headers(1), json=draft)
    assert result.status_code == 202, result.text
    draft_task_id = result.json()["task_id"]
    model = Model()
    await worker.run_once(db_sessionmaker, model)
    result = db_client.get("/assistant/tasks/" + draft_task_id, headers=auth_headers(1)).json()
    assert result["state"] == "succeeded", result
    assert not model.calls
    draft_id = result["artifact_id"]
    view = db_client.get("/assistant/artifacts/" + draft_id, headers=auth_headers(1)).json()
    assert "Discuss availability" in view["artifact"]["content"]["body"]
    assert "Ask about meeting duration" not in view["artifact"]["content"]["body"]
    assert not view["review"]["blockers"]
    # Exact source metadata participates in email preview freshness too.
    from app.actions.email_preview import sources

    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, draft_task_id)
        stored = await session.get(ArtifactRevision, draft_id)
        _, metadata = await sources(session, task, stored)
        assert metadata["contexts"]["plan_grounding"]["artifact_id"] == accepted["artifact_id"]
    changed = {
        "request_id": "edit-plan",
        "expected_revision": 2,
        "items": [
            {
                "id": item["id"],
                "text": "Check with the team",
                "owner": "Me",
                "due_date": "2026-12-01",
                "depends_on": [],
            }
        ],
    }
    result = db_client.post(url, headers=auth_headers(1), json=changed)
    assert result.status_code == 200, result.text
    assert result.json()["artifact"]["content"]["accepted_item_ids"] == []
    view = db_client.get("/assistant/artifacts/" + draft_id, headers=auth_headers(1)).json()
    assert "accepted_plan_changed" in view["review"]["blockers"]


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "a", "depends_on": ["b"]}],
        [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
        [{"id": "a", "depends_on": []}, {"id": "a", "depends_on": []}],
    ],
)
def test_plan_review_dependencies(items):
    with pytest.raises(ValueError):
        planning.check_dependencies(items)
