"""Regression evidence for the independent source-bridge review findings."""
# ruff: noqa: F811

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.assistant import source_data
from app.conversation import service, store
from app.db.models import AssistantAction, Conversation, User
from tests.test_calendar_creation import turn
from tests.test_calendar_email_context import inspection, prepare  # noqa: F401
from tests.test_calendar_email_context_boundaries import follow
from tests.test_conversation import Model, tool
from tests.test_meeting_email_event import configured, setup  # noqa: F401
from tests.test_shared_mail_context import pinned


@pytest.mark.parametrize("qualifier, zone", [("AEST", "Etc/GMT-10"), ("AEDT", "Etc/GMT-11")])
async def test_short_clock_quote_cannot_drop_email_timezone(
    inspection, db_sessionmaker, qualifier, zone
):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM " + qualifier)
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote
    # The field quote intentionally omits the attached zone, just as in review.
    _, result = await prepare(db_sessionmaker, fields)
    assert result["kind"] == "calendar_event", result
    event = result["calendar_action"]["preview"]["event"]
    assert event["start"]["timeZone"] == zone
    start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(ZoneInfo(zone))
    assert (start.hour, start.minute) == (8, 30)
    assert "saved timezone" not in result["text"]


async def test_unknown_source_timezone_never_falls_back_to_saved_zone(inspection, db_sessionmaker):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM CST")
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote
    _, result = await prepare(db_sessionmaker, fields)
    assert result["kind"] == "clarification", result
    assert "timezone" in result["text"]


async def test_repeating_accepted_generated_title_preserves_its_provenance(
    inspection, db_sessionmaker
):
    _, fields, _ = inspection
    fields["title"] = "Inspection appointment"
    fields.pop("time")
    fields.pop("time_source")
    fields["email_source"]["fields"] = [
        f for f in fields["email_source"]["fields"] if f["field"] != "time"
    ]
    request, result = await prepare(db_sessionmaker, fields)
    assert "time" in result["text"]
    _, result = await follow(
        db_sessionmaker,
        request,
        result,
        "9am",
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            title="Inspection appointment",
            time="09:00",
            time_source="9am",
        ),
    )
    assert result["kind"] == "calendar_event", result
    assert result["calendar_action"]["preview"]["event"]["summary"] == "Inspection appointment"


@pytest.mark.parametrize("error", ["date", "location"])
async def test_source_field_repair_preserves_other_verified_fields_without_resupply(
    inspection, db_sessionmaker, error
):
    _, fields, quote = inspection
    broken = deepcopy(fields)
    if error == "date":
        broken["date"]["start"] = "2027-12-25"
        repaired = {"date": fields["date"], "date_source": fields["date_source"]}
    else:
        broken["email_source"]["fields"][-1]["quote"] = "Never in this email"
        repaired = {"location": fields["location"]}
    repaired["email_source"] = {
        **fields["email_source"],
        "fields": [item for item in fields["email_source"]["fields"] if item["field"] == error],
    }
    async with source_data.source_scope():
        capture = await pinned(db_sessionmaker)
    request = turn(
        "create an event from this", context_snapshot_id=capture.id, timezone="Australia/Melbourne"
    )
    async with source_data.source_scope():
        result = await service.turn(
            1,
            request,
            factory=db_sessionmaker,
            model=Model(
                tool("read_email", reference="selected", scope="selected_message"),
                tool("prepare_calendar_event", **broken),
                tool("prepare_calendar_event", **repaired),
            ),
        )
    assert result["kind"] == "calendar_event", result
    event = result["calendar_action"]["preview"]["event"]
    assert event["summary"] == "Property inspection"
    assert event["location"] == "20 Example St"
    assert result["trace"][1]["status"] == "invalid"
    async with db_sessionmaker() as db:
        pending = store.decode(await db.get(Conversation, request.conversation_id))[
            "calendar_event_request"
        ]
        assert pending["arguments"]["time"] == "08:30"
        assert pending["field_provenance"]["title"]["kind"] == "email"


@pytest.mark.parametrize("state", ["succeeded", "outcome_unknown"])
async def test_durable_calendar_receipt_review_survives_lost_gmail_access(
    inspection, db_sessionmaker, db_client, auth_headers, state
):
    _, fields, _ = inspection
    request, result = await prepare(db_sessionmaker, fields)
    async with db_sessionmaker.begin() as db:
        action = await db.get(AssistantAction, result["calendar_action_id"])
        action.state = state
        if state == "succeeded":
            action.result = {"event_id": "fake-event"}
        user = await db.get(User, 1)
        user.google_scopes = [scope for scope in user.google_scopes if "gmail" not in scope]
        saved = store.decode(await db.get(Conversation, request.conversation_id))
        goal_id = saved["calendar_event_request"]["goal_id"]
    async with source_data.source_scope():
        replay = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert replay["calendar_action"]["state"] == state
    _, reviewed = await follow(
        db_sessionmaker,
        request,
        result,
        "Review the inspection",
        tool("review_conversation_goal", goal_id=goal_id, source="Review the inspection"),
    )
    assert reviewed["calendar_action"]["state"] == state, reviewed
    response = db_client.get(
        "/assistant/calendar-actions/" + result["calendar_action_id"], headers=auth_headers(1)
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == state


async def test_event_quote_boundary_cannot_cut_off_a_clock_qualifier(inspection, db_sessionmaker):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM AEST")
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote.split(" AEST")[0]
    fields.pop("location")
    fields["email_source"]["fields"] = [
        f for f in fields["email_source"]["fields"] if f["field"] != "location"
    ]
    _, result = await prepare(db_sessionmaker, fields)
    assert result["kind"] == "calendar_event", result
    assert result["calendar_action"]["preview"]["event"]["start"]["timeZone"] == "Etc/GMT-10"


async def test_unknown_timezone_remains_required_until_user_clarifies(inspection, db_sessionmaker):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM CST")
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote
    request, result = await prepare(db_sessionmaker, fields)
    request, result = await follow(
        db_sessionmaker,
        request,
        result,
        "use the saved date",
        tool("prepare_calendar_event", continue_previous=True),
    )
    assert result["kind"] == "clarification" and "timezone" in result["text"], result
    _, result = await follow(
        db_sessionmaker,
        request,
        result,
        "Australia/Melbourne",
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            timezone="Australia/Melbourne",
            timezone_source="Australia/Melbourne",
        ),
    )
    assert result["kind"] == "calendar_event", result


async def test_explicit_user_timezone_correction_overrides_source(inspection, db_sessionmaker):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM AEST")
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote
    request, result = await prepare(db_sessionmaker, fields)
    text = "Use Australia/Melbourne timezone"
    _, revised = await follow(
        db_sessionmaker,
        request,
        result,
        text,
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            intent={"operation": "revise", "source": text},
            changes=[
                {
                    "field": "timezone",
                    "operation": "replace",
                    "value": "Australia/Melbourne",
                    "source": "Australia/Melbourne",
                }
            ],
        ),
    )
    assert revised["kind"] == "calendar_event", revised
    assert (
        revised["calendar_action"]["preview"]["event"]["start"]["timeZone"] == "Australia/Melbourne"
    )


@pytest.mark.parametrize("correction", ["time", "clear_location"])
async def test_later_source_envelope_cannot_undo_user_replacement_or_clear(
    inspection, db_sessionmaker, correction
):
    _, fields, _ = inspection
    request, result = await prepare(db_sessionmaker, fields)
    if correction == "time":
        text = "Move it to 9am"
        change = {"field": "time", "operation": "replace", "value": "09:00", "source": "9am"}
        later_field = "time"
        later_values = {"time": fields["time"], "time_source": fields["time_source"]}
    else:
        text = "Clear the location"
        change = {"field": "location", "operation": "clear", "source": "Clear the location"}
        later_field = "location"
        later_values = {"location": fields["location"]}
    request, result = await follow(
        db_sessionmaker,
        request,
        result,
        text,
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            intent={"operation": "revise", "source": text},
            changes=[change],
        ),
    )
    assert result["kind"] == "calendar_event", result
    if correction == "time":
        text = "Move it to Room B"
        revision = {
            "field": "location",
            "operation": "replace",
            "value": "Room B",
            "source": "Room B",
        }
    else:
        text = "Rename it Visit"
        revision = {"field": "title", "operation": "replace", "value": "Visit", "source": "Visit"}
    _, revised = await follow(
        db_sessionmaker,
        request,
        result,
        text,
        tool("read_email", reference="selected", scope="selected_message"),
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            intent={"operation": "revise", "source": text},
            changes=[revision],
            **later_values,
            email_source={
                **fields["email_source"],
                "fields": [
                    item
                    for item in fields["email_source"]["fields"]
                    if item["field"] == later_field
                ],
            },
        ),
    )
    assert revised["kind"] == "calendar_event", revised
    event = revised["calendar_action"]["preview"]["event"]
    if correction == "time":
        assert event["location"] == "Room B"
        start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
            ZoneInfo("Australia/Melbourne")
        )
        assert (start.hour, start.minute) == (9, 0)
    else:
        assert event["summary"] == "Visit"
        assert event["location"] == ""


async def test_shorter_source_envelope_does_not_clear_unresolved_timezone(
    inspection, db_sessionmaker
):
    configured, fields, quote = inspection
    quote = quote.replace("8:30 AM", "8:30 AM CST")
    configured[0].text = quote
    fields["email_source"]["event_quote"] = quote
    request, result = await prepare(db_sessionmaker, fields)
    assert result["kind"] == "clarification" and "timezone" in result["text"]
    shortened = {**fields["email_source"], "event_quote": quote.split(" 8:30")[0], "fields": []}
    request, result = await follow(
        db_sessionmaker,
        request,
        result,
        "Use the email details",
        tool("read_email", reference="selected", scope="selected_message"),
        tool("prepare_calendar_event", continue_previous=True, email_source=shortened),
    )
    assert result["kind"] == "clarification" and "timezone" in result["text"], result
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["email_source"]["timezone_required"]
