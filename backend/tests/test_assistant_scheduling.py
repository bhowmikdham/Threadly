"""Scheduling routes, durable worker, typed answers and real DB fences; Google is fake."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.assistant import scheduling, tasks, worker
from app.calendar import negotiations, service, slots
from app.db.models import (
    ArtifactRevision,
    AssistantAction,
    AssistantJob,
    CalendarPreference,
    CalendarSlotRequest,
    Message,
    TaskInput,
    TaskQuestion,
    Thread,
    User,
)
from app.schemas.calendar import SavePreferences
from app.schemas.scheduling import SchedulingInputRequest, SchedulingRequest
from tests.conftest import needs_pg
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_slot_service import saved

pytestmark = needs_pg


@pytest.fixture()
def setup(calendar_setup, db_sessionmaker, monkeypatch):  # noqa: F811
    monkeypatch.setattr(slots, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(negotiations, "get_session_factory", lambda: db_sessionmaker)

    async def seed():
        await service.save_preferences(1, SavePreferences.model_validate(saved()))
        async with db_sessionmaker() as session:
            session.add(
                Thread(
                    id=1,
                    user_id=1,
                    gmail_thread_id="schedule-thread",
                    version=1,
                    last_msg_id="schedule-message",
                )
            )
            await session.flush()
            session.add(
                Message(
                    user_id=1,
                    thread_id=1,
                    gmail_msg_id="schedule-message",
                    body_clean="Are you free tomorrow at four?",
                    sent_at=datetime.now(UTC) - timedelta(days=1),
                    is_from_user=False,
                )
            )
            await session.commit()

    asyncio.run(seed())
    calendar_setup.calls.clear()
    return calendar_setup


def body(**changes):
    return {
        "schema_version": "1.0",
        "request_id": "schedule-1",
        "operation": "suggest_slots",
        "expected_preferences_version": 1,
        "constraints": {"date": "tomorrow"},
        **changes,
    }


def submit(client, headers, **changes):
    result = client.post("/assistant/scheduling-requests", headers=headers(1), json=body(**changes))
    assert result.status_code == 202, result.text
    return result.json()


def run(factory):
    assert asyncio.run(worker.run_once(factory))


def get_task(client, headers, task):
    return client.get(f"/assistant/tasks/{task['task_id']}", headers=headers(1)).json()


def artifact(client, headers, task):
    result = client.get(f"/assistant/artifacts/{task['artifact_id']}", headers=headers(1))
    assert result.status_code == 200, result.text
    return result.json()


def capture(client, headers):
    response = client.post(
        "/assistant/context-snapshots",
        headers=headers(1),
        json={"schema_version": "1.0", "thread_id": "schedule-thread"},
    )
    assert response.status_code == 201, response.text
    return response.json()["context_snapshot_id"]


def answer_body(task, answer, key="answer-1"):
    return {
        "schema_version": "1.0",
        "request_id": key,
        "expected_version": task["version"],
        "question_id": task["question"]["question_id"],
        "answer": answer,
    }


def answer(client, headers, task, values, key="answer-1"):
    result = client.post(
        task["question"]["input_url"], headers=headers(1), json=answer_body(task, values, key)
    )
    assert result.status_code == 202, result.text
    return result.json()


def test_worker_options_artifact_offer_bridge_and_no_actions(
    db_client, auth_headers, db_sessionmaker, setup
):
    context = capture(db_client, auth_headers)
    task = submit(db_client, auth_headers, context_snapshot_id=context)
    assert task["state"] == "queued" and not setup.calls
    run(db_sessionmaker)
    task = get_task(db_client, auth_headers, task)
    assert task["state"] == "succeeded", task
    result = artifact(db_client, auth_headers, task)
    content = result["artifact"]["content"]
    assert result["artifact"]["kind"] == "schedule_options"
    assert result["scheduling_status"] == {
        "usable": True,
        "blockers": [],
        "has_available_options": True,
    }
    assert len(content["slots"]) == 3 and content["attendee_availability"] == "unknown"
    assert content["event_created"] is False and content["booking_approved"] is False
    neg = db_client.post(
        "/calendar/negotiations",
        headers=auth_headers(1),
        json={
            "request_id": "neg",
            "thread_id": "schedule-thread",
            "expected_thread_version": 1,
        },
    ).json()
    offer = db_client.post(
        f"/calendar/negotiations/{neg['id']}/offers",
        headers=auth_headers(1),
        json={
            "request_id": "offer",
            "expected_version": 1,
            "expected_thread_version": 1,
            "slot_request_id": content["slot_request_id"],
        },
    )
    assert offer.status_code == 201, offer.text
    assert [s["id"] for s in offer.json()["slots"]] == [s["id"] for s in content["slots"]]
    events = db_client.get(task["events_url"], headers=auth_headers(1)).text
    assert "task.routed" in events and "artifact.ready" in events
    assert (
        db_client.get(
            f"/assistant/artifacts/{task['artifact_id']}", headers=auth_headers(2)
        ).status_code
        == 404
    )

    async def counts():
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0

    asyncio.run(counts())


def test_ambiguous_four_clarifies_then_resumes_original_goal(
    db_client, auth_headers, db_sessionmaker, setup
):
    task = submit(
        db_client,
        auth_headers,
        operation="check_time",
        constraints={"date": "tomorrow", "at_time": "4"},
    )
    run(db_sessionmaker)
    stopped = get_task(db_client, auth_headers, task)
    assert stopped["state"] == "needs_clarification" and stopped["question"]["fields"] == [
        "meridiem"
    ]
    assert not setup.calls
    original_anchor = stopped["scheduling"]["anchor_at"]
    continued = answer(db_client, auth_headers, stopped, {"meridiem": "PM"})
    assert continued["input_version"] == 1
    run(db_sessionmaker)
    completed = get_task(db_client, auth_headers, task)
    assert completed["state"] == "succeeded", completed
    result = artifact(db_client, auth_headers, completed)["artifact"]
    assert result["kind"] == "availability"
    assert "T16:00:00" in result["content"]["slots"][0]["start"]
    assert result["content"]["anchor_at"] == original_anchor
    assert answer(db_client, auth_headers, stopped, {"meridiem": "PM"})["state"] == "succeeded"
    assert sum(req.url.path.endswith("freeBusy") for req in setup.calls) == 1


def test_context_resolves_four_without_question(db_client, auth_headers, db_sessionmaker, setup):
    task = submit(
        db_client,
        auth_headers,
        operation="check_time",
        constraints={
            "date": "tomorrow",
            "at_time": "4",
            "time_context": {"start_minute": 900, "end_minute": 1080},
        },
    )
    run(db_sessionmaker)
    completed = get_task(db_client, auth_headers, task)
    assert completed["state"] == "succeeded", completed
    result = artifact(db_client, auth_headers, completed)["artifact"]["content"]
    assert result["resolution"]["assumptions"][-1]["source"] == "explicit_time_context"


def test_missing_fields_partial_answers_and_invalid_extra_input(
    db_client, auth_headers, db_sessionmaker, setup
):
    task = submit(db_client, auth_headers, operation="check_time", constraints={})
    run(db_sessionmaker)
    stopped = get_task(db_client, auth_headers, task)
    assert stopped["question"]["fields"] == ["date", "at_time"]
    bad = db_client.post(
        stopped["question"]["input_url"],
        headers=auth_headers(1),
        json=answer_body(stopped, {"date": "next time"}),
    )
    assert bad.status_code == 422
    assert get_task(db_client, auth_headers, task)["input_version"] == 0
    answer(db_client, auth_headers, stopped, {"date": "tomorrow"})
    run(db_sessionmaker)
    second = get_task(db_client, auth_headers, task)
    assert second["question"]["fields"] == ["at_time"]
    wrong = db_client.post(
        second["question"]["input_url"],
        headers=auth_headers(1),
        json=answer_body(second, {"date": "today"}, "wrong"),
    )
    assert wrong.status_code == 422
    answer(db_client, auth_headers, second, {"at_time": "16:00"}, "answer-2")
    run(db_sessionmaker)
    assert get_task(db_client, auth_headers, task)["state"] == "succeeded"


@pytest.mark.parametrize("change", ["source", "preferences", "account", "scope", "cancel"])
def test_inflight_change_blocks_publication(
    db_client, auth_headers, db_sessionmaker, setup, change
):
    task = submit(db_client, auth_headers, context_snapshot_id=capture(db_client, auth_headers))

    async def mutate(req):
        if not req.url.path.endswith("freeBusy"):
            return
        async with db_sessionmaker.begin() as session:
            if change == "source":
                row = await session.get(Thread, 1)
                row.version += 1
            elif change == "preferences":
                row = await session.get(CalendarPreference, 1)
                row.version += 1
            elif change in {"account", "scope"}:
                row = await session.get(User, 1)
                if change == "account":
                    row.google_account_version += 1
                else:
                    row.google_scopes = []
            else:
                row = await tasks.owned_task(session, 1, task["task_id"], lock=True)
                await tasks.cancel(session, 1, row.id, row.version)

    setup.callback = mutate
    run(db_sessionmaker)
    completed = get_task(db_client, auth_headers, task)
    assert completed["state"] == ("cancelled" if change == "cancel" else "failed"), completed
    assert completed["artifact_id"] is None


def test_unknown_and_fewer_options_report_honestly(db_client, auth_headers, db_sessionmaker, setup):
    setup.unknown = True
    first = submit(db_client, auth_headers)
    run(db_sessionmaker)
    content = artifact(db_client, auth_headers, get_task(db_client, auth_headers, first))[
        "artifact"
    ]["content"]
    assert content["status"] == "unknown" and content["slots"] == []
    setup.unknown = False
    values = saved(1)
    values["preferences"]["working_periods"] = [
        {"weekday": i, "start_minute": 900, "end_minute": 930} for i in range(7)
    ]
    asyncio.run(service.save_preferences(1, SavePreferences.model_validate(values)))
    second = submit(db_client, auth_headers, request_id="fewer", expected_preferences_version=2)
    run(db_sessionmaker)
    content = artifact(db_client, auth_headers, get_task(db_client, auth_headers, second))[
        "artifact"
    ]["content"]
    assert len(content["slots"]) == 1 and content["reason"] == "insufficient_slots"


def test_source_message_anchor_and_stale_capture(db_client, auth_headers, db_sessionmaker, setup):
    context = capture(db_client, auth_headers)
    task = submit(
        db_client, auth_headers, context_snapshot_id=context, anchor_message_id="schedule-message"
    )
    assert task["scheduling"]["anchor_source"] == "source_message"
    received = datetime.fromisoformat(task["scheduling"]["anchor_at"])
    assert received.date() == (datetime.now(UTC) - timedelta(days=1)).date()
    run(db_sessionmaker)
    content = artifact(db_client, auth_headers, get_task(db_client, auth_headers, task))[
        "artifact"
    ]["content"]
    assert datetime.fromisoformat(
        content["resolution"]["start"]
    ).date() == received.date() + timedelta(days=1)

    async def change():
        async with db_sessionmaker.begin() as session:
            thread = await session.get(Thread, 1)
            thread.version += 1

    asyncio.run(change())
    stale = db_client.post(
        "/assistant/scheduling-requests",
        headers=auth_headers(1),
        json=body(
            request_id="stale",
            context_snapshot_id=context,
        ),
    )
    assert stale.status_code == 409


@pytest.mark.parametrize("change", ["expiry", "source", "scope"])
def test_historical_artifact_has_current_usability(
    db_client, auth_headers, db_sessionmaker, setup, change
):
    task = submit(db_client, auth_headers, context_snapshot_id=capture(db_client, auth_headers))
    run(db_sessionmaker)
    task = get_task(db_client, auth_headers, task)
    original = artifact(db_client, auth_headers, task)

    async def mutate():
        async with db_sessionmaker.begin() as session:
            if change == "expiry":
                row = await session.scalar(select(CalendarSlotRequest))
                row.created_at -= timedelta(minutes=10)
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            elif change == "source":
                row = await session.get(Thread, 1)
                row.version += 1
            else:
                row = await session.get(User, 1)
                row.google_scopes = []

    asyncio.run(mutate())
    result = artifact(db_client, auth_headers, task)
    assert result["artifact"] == original["artifact"]
    assert (
        result["scheduling_status"]["usable"] is False and result["scheduling_status"]["blockers"]
    )


def test_concurrent_answers_idempotency_and_ownership(
    db_client, auth_headers, db_sessionmaker, setup
):
    task = submit(
        db_client,
        auth_headers,
        operation="check_time",
        constraints={"date": "tomorrow", "at_time": "4"},
    )
    run(db_sessionmaker)
    stopped = get_task(db_client, auth_headers, task)
    value = answer_body(stopped, {"meridiem": "PM"})
    assert (
        db_client.post(
            stopped["question"]["input_url"], headers=auth_headers(2), json=value
        ).status_code
        == 404
    )

    async def accept():
        async with db_sessionmaker.begin() as session:
            return (
                await scheduling.accept_input(
                    session, 1, task["task_id"], SchedulingInputRequest.model_validate(value)
                )
            ).id

    async def race():
        assert len(set(await asyncio.gather(accept(), accept()))) == 1
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(TaskInput)) == 1

    asyncio.run(race())
    value["answer"] = {"meridiem": "AM"}
    assert (
        db_client.post(
            stopped["question"]["input_url"], headers=auth_headers(1), json=value
        ).status_code
        == 409
    )


def test_acceptance_replay_conflict_and_concurrent_jobs(
    db_client, auth_headers, db_sessionmaker, setup
):
    task = submit(db_client, auth_headers)
    assert submit(db_client, auth_headers)["task_id"] == task["task_id"]
    assert (
        db_client.post(
            "/assistant/scheduling-requests",
            headers=auth_headers(1),
            json=body(constraints={"date": "today"}),
        ).status_code
        == 409
    )

    async def race():
        async def accept():
            async with db_sessionmaker.begin() as session:
                req = SchedulingRequest.model_validate(body(request_id="raced"))
                return (await tasks.submit(session, 1, req.as_request(), schedule=req)).id

        assert len(set(await asyncio.gather(accept(), accept()))) == 1
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(AssistantJob)) == 2

    asyncio.run(race())


@pytest.mark.parametrize(
    "extra",
    [
        {"instruction": "book it now"},
        {"approved": True},
        {"constraints": {"date": "tomorrow", "slot_id": "invented"}},
        {"anchor_message_id": "foreign"},
        {"constraints": {"date": "", "at_time": "4"}},
    ],
)
def test_untrusted_or_unsupported_fields_rejected(db_client, auth_headers, setup, extra):
    response = db_client.post(
        "/assistant/scheduling-requests", headers=auth_headers(1), json=body(**extra)
    )
    assert response.status_code == 422
    assert not setup.calls


def test_no_permission_or_foreign_snapshot(db_client, auth_headers, db_sessionmaker, setup):
    assert db_client.post("/assistant/scheduling-requests", json=body()).status_code == 401
    context = capture(db_client, auth_headers)
    assert (
        db_client.post(
            "/assistant/scheduling-requests",
            headers=auth_headers(2),
            json=body(context_snapshot_id=context),
        ).status_code
        == 404
    )

    async def revoke():
        async with db_sessionmaker.begin() as session:
            user = await session.get(User, 1)
            user.google_scopes = []

    asyncio.run(revoke())
    assert (
        db_client.post(
            "/assistant/scheduling-requests", headers=auth_headers(1), json=body()
        ).status_code
        == 403
    )
    assert not setup.calls


def test_expired_lease_reuses_completed_query(
    db_client, auth_headers, db_sessionmaker, setup, monkeypatch
):
    task = submit(db_client, auth_headers)
    original_finish = tasks.finish

    async def drop_publication(session, claim, **kwargs):
        if kwargs.get("payload"):
            return False
        return await original_finish(session, claim, **kwargs)

    monkeypatch.setattr(tasks, "finish", drop_publication)
    run(db_sessionmaker)

    async def expire():
        async with db_sessionmaker.begin() as session:
            job = await session.get(AssistantJob, task["task_id"])
            job.lease_expires_at = (
                await session.scalar(select(func.clock_timestamp()))
            ) - timedelta(seconds=1)

    asyncio.run(expire())
    monkeypatch.setattr(tasks, "finish", original_finish)
    run(db_sessionmaker)
    assert get_task(db_client, auth_headers, task)["state"] == "succeeded"
    assert sum(req.url.path.endswith("freeBusy") for req in setup.calls) == 1


def test_saved_working_hours_resolve_four_in_task(db_client, auth_headers, db_sessionmaker, setup):
    values = saved(1)
    values["preferences"]["working_periods"] = [
        {"weekday": day, "start_minute": 540, "end_minute": 1020} for day in range(7)
    ]
    asyncio.run(service.save_preferences(1, SavePreferences.model_validate(values)))
    task = submit(
        db_client,
        auth_headers,
        expected_preferences_version=2,
        operation="check_time",
        constraints={"date": "tomorrow", "at_time": "4"},
    )
    run(db_sessionmaker)
    completed = get_task(db_client, auth_headers, task)
    assert completed["state"] == "succeeded", completed
    content = artifact(db_client, auth_headers, completed)["artifact"]["content"]
    assert content["resolution"]["assumptions"][-1]["source"] == "saved_working_hours"
    assert "T16:00:00" in content["slots"][0]["start"]


@pytest.mark.parametrize("change", ["cancel", "expiry", "source"])
def test_answers_cannot_revive_cancelled_expired_or_stale_task(
    db_client,
    auth_headers,
    db_sessionmaker,
    setup,
    change,
):
    task = submit(
        db_client,
        auth_headers,
        constraints={},
        context_snapshot_id=capture(db_client, auth_headers),
    )
    run(db_sessionmaker)
    stopped = get_task(db_client, auth_headers, task)

    async def alter():
        async with db_sessionmaker.begin() as session:
            if change == "cancel":
                await tasks.cancel(session, 1, task["task_id"], stopped["version"])
            elif change == "expiry":
                row = await session.get(TaskQuestion, stopped["question"]["question_id"])
                row.expires_at = (await session.scalar(select(func.clock_timestamp()))) - timedelta(
                    seconds=1
                )
            else:
                row = await session.get(Thread, 1)
                row.version += 1

    asyncio.run(alter())
    result = db_client.post(
        stopped["question"]["input_url"],
        headers=auth_headers(1),
        json=answer_body(stopped, {"date": "tomorrow"}),
    )
    assert result.status_code == 409
    assert get_task(db_client, auth_headers, task)["input_version"] == 0
    assert not setup.calls


def test_clarification_round_limit_is_bounded(db_client, auth_headers, db_sessionmaker, setup):
    task = submit(db_client, auth_headers, constraints={"date": "9999-12-31"})
    for i in range(5):
        run(db_sessionmaker)
        stopped = get_task(db_client, auth_headers, task)
        assert stopped["question"]["fields"] == ["date"]
        answer(db_client, auth_headers, stopped, {"date": "9999-12-31"}, key=f"answer-{i}")
    run(db_sessionmaker)
    final = get_task(db_client, auth_headers, task)
    assert final["state"] == "unsupported" and final["error_code"] == "clarification_limit_reached"
    assert not setup.calls


def test_publication_rollback_has_no_orphan_and_reuses_read(
    db_client,
    auth_headers,
    db_sessionmaker,
    setup,
    monkeypatch,
):
    task = submit(db_client, auth_headers)
    original = tasks.finish

    async def crash(session, claim, **kwargs):
        result = await original(session, claim, **kwargs)
        if kwargs.get("payload"):
            raise SQLAlchemyError("simulated publication rollback")
        return result

    monkeypatch.setattr(tasks, "finish", crash)
    with pytest.raises(SQLAlchemyError):
        run(db_sessionmaker)

    async def inspect():
        async with db_sessionmaker.begin() as session:
            assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 0
            assert await session.scalar(select(func.count()).select_from(CalendarSlotRequest)) == 1
            job = await session.get(AssistantJob, task["task_id"])
            job.lease_expires_at = (
                await session.scalar(select(func.clock_timestamp()))
            ) - timedelta(seconds=1)

    asyncio.run(inspect())
    monkeypatch.setattr(tasks, "finish", original)
    run(db_sessionmaker)
    assert get_task(db_client, auth_headers, task)["state"] == "succeeded"
    assert sum(req.url.path.endswith("freeBusy") for req in setup.calls) == 1


def test_query_result_must_belong_to_exact_task(
    db_client, auth_headers, db_sessionmaker, setup, monkeypatch
):
    from app.schemas.slots import SlotRequest

    unrelated = asyncio.run(
        slots.submit(
            1,
            SlotRequest(
                request_id="unrelated",
                expected_preferences_version=1,
                date="tomorrow",
                count=1,
            ),
        )
    )

    async def substitute(*args):
        return unrelated

    monkeypatch.setattr(slots, "submit", substitute)
    task = submit(db_client, auth_headers)
    run(db_sessionmaker)
    final = get_task(db_client, auth_headers, task)
    assert final["state"] == "failed" and final["error_code"] == "calendar_query_mismatch"
    assert final["artifact_id"] is None


def test_internal_route_stays_explicit_and_wrong_answer_endpoint_fails(
    db_client,
    auth_headers,
    db_sessionmaker,
    setup,
):
    task = submit(db_client, auth_headers, constraints={})
    run(db_sessionmaker)
    stopped = get_task(db_client, auth_headers, task)
    wrong = db_client.post(
        f"/assistant/tasks/{task['task_id']}/inputs",
        headers=auth_headers(1),
        json={
            **answer_body(stopped, {"date_phrase": "tomorrow"}),
        },
    )
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "scheduling_input_required"
    config = db_client.get("/assistant/workflows", headers=auth_headers(1)).json()["scheduling"]
    assert config["typed_constraints_required"] and config["natural_language_extraction"] is True
    assert config["extraction"]["requires_complete_request_review"]
    assert config["extraction"]["entrypoint"] == "/assistant/scheduling-proposals"


def test_saved_anchor_timezone_and_dst_questions_are_preserved():
    from app.calendar import time_resolution
    from app.schemas.calendar import Preferences
    from app.schemas.scheduling import SchedulingConstraints

    prefs = saved()["preferences"]
    snapshot = {
        "request": {"expected_preferences_version": 1},
        "preferences": prefs,
        "anchor_at": "2026-10-31T22:30:00+00:00",
    }
    constraints = SchedulingConstraints(
        date="tomorrow", at_time="01:30", timezone="America/New_York"
    )
    query = scheduling.query_body("test", 0, snapshot, constraints)
    assert query.date == "2026-11-01"
    resolution = time_resolution.resolve(
        query, Preferences.model_validate(prefs), datetime.now(UTC)
    )
    question = scheduling.resolution_question(resolution)
    assert question["fields"] == ["fold"] and len(question["choices"]) == 2
    assert question["choices"][0]["start"] != question["choices"][1]["start"]
    corrected = SchedulingConstraints.model_validate({**constraints.model_dump(), "fold": 1})
    after = scheduling.query_body("test", 1, snapshot, corrected)
    resolved = time_resolution.resolve(after, Preferences.model_validate(prefs), datetime.now(UTC))
    assert resolved["start"] == question["choices"][1]["start"]
    assert after.date == query.date  # An answer cannot reanchor tomorrow at worker execution time.
