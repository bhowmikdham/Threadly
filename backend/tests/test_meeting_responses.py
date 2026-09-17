"""Historical offer interpretation never treats stale availability as current."""

# ruff: noqa: F811
import json
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.assistant import command_plans, meeting_responses, worker
from app.db.models import AssistantAction, AssistantTask, MeetingSelection, Message, Thread
from app.model_client.client import GenResult
from app.schemas.meeting_response import MeetingResponseRequest
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_actions import proposed
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401


class Model:
    def __init__(self, option=2):
        self.option = option

    async def generate(self, *args, **kwargs):
        return json.dumps(
            {
                "status": "choice" if self.option else "ambiguous",
                "option": self.option,
                "quote": "The second option works for me." if self.option else "",
            }
        ), GenResult("fake", "choice-fixture")


@needs_pg
async def test_later_email_choice_review_rechecks_original_time(
    db_sessionmaker, setup, db_client, auth_headers
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    async with db_sessionmaker.begin() as session:
        stored = await session.get(AssistantAction, action["action_id"])
        selection = await session.get(MeetingSelection, stored.source_versions["selection_id"])
        offer_id = selection.offer_id
        version = stored.source_versions["negotiation_version"]
        await session.execute(update(Thread).values(version=2, updated_at=func.clock_timestamp()))
        await session.execute(
            update(Message).values(
                body_clean="The second option works for me.", sent_at=datetime.now(UTC)
            )
        )
    context = capture(db_client, auth_headers)
    req = MeetingResponseRequest(
        schema_version="1.0",
        request_id="reply-choice",
        context_snapshot_id=context,
        message_id="schedule-message",
        offer_id=offer_id,
        expected_negotiation_version=version,
        expected_preferences_version=1,
    )
    async with db_sessionmaker.begin() as session:
        row, created = await meeting_responses.reserve(session, 1, req)
    assert created
    state, result = await meeting_responses.interpret(row, Model())
    assert state == "proposed", result
    assert not result["external_actions"]
    async with db_sessionmaker.begin() as session:
        row = await command_plans.complete(session, 1, row.id, state, result)
    path = f"/assistant/meeting-response-proposals/{row.id}/confirm"
    confirmation = {"plan_hash": row.plan_hash, "confirm_complete_command": True}
    assert db_client.post(path, headers=auth_headers(2), json=confirmation).status_code == 404
    response = db_client.post(path, headers=auth_headers(1), json=confirmation)
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]
    assert (
        db_client.post(path, headers=auth_headers(1), json=confirmation).json()["task_id"]
        == task_id
    )
    await worker.run_once(db_sessionmaker)
    response = db_client.get("/assistant/tasks/" + task_id, headers=auth_headers(1)).json()
    assert response["state"] == "succeeded", response
    artifact = db_client.get(
        "/assistant/artifacts/" + response["artifact_id"], headers=auth_headers(1)
    ).json()["artifact"]
    assert artifact["meeting_choice"]["availability_confirmed"]
    assert not artifact["meeting_choice"]["booking_approved"]
    assert artifact["meeting_choice"]["slot_id"] == result["selected_slot"]["id"]
    assert artifact["content"]["slots"][0]["start"] == result["selected_slot"]["start"]
    async with db_sessionmaker() as session:
        assert (
            await session.scalar(select(func.count()).select_from(AssistantAction)) == 1
        )  # original preview only
        assert await session.scalar(select(func.count()).select_from(MeetingSelection)) == 1
        assert (await session.get(AssistantTask, task_id)).final_artifact_id == response[
            "artifact_id"
        ]
