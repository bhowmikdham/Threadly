"""Owned API -> saved request -> Google read -> deterministic slot publication."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.errors import ApiError
from app.calendar import service, slots
from app.db.models import CalendarEvidence, CalendarPreference, CalendarSlotRequest, User
from app.schemas.calendar import SavePreferences
from app.schemas.slots import SlotRequest
from tests.conftest import needs_pg
from tests.test_calendar_service import save_body
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401

pytestmark = needs_pg


def saved(version=0, **changes):
    return save_body(
        version,
        timezone="UTC",
        working_periods=[
            {"weekday": day, "start_minute": 0, "end_minute": 1440} for day in range(7)
        ],
        buffer_before_minutes=15,
        buffer_after_minutes=20,
        minimum_notice_minutes=0,
        **changes,
    )


def body(**changes):
    return {
        "request_id": "slots-1",
        "date": "tomorrow",
        "expected_preferences_version": 1,
        **changes,
    }


@pytest.fixture()
def setup(calendar_setup, db_sessionmaker, monkeypatch):  # noqa: F811
    monkeypatch.setattr(slots, "get_session_factory", lambda: db_sessionmaker)
    asyncio.run(service.save_preferences(1, SavePreferences.model_validate(saved())))
    calendar_setup.calls.clear()
    return calendar_setup


def post(db_client, auth_headers, **changes):
    return db_client.post("/calendar/slot-requests", headers=auth_headers(1), json=body(**changes))


def test_roundtrip_padding_stable_ids_ownership_and_idempotency(db_client, auth_headers, setup):
    assert db_client.post("/calendar/slot-requests", json=body()).status_code == 401
    response = post(db_client, auth_headers, participant_timezones=["Australia/Melbourne"])
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    receipt = response.json()
    assert receipt["state"] == "complete" and len(receipt["slots"]) == 3
    assert receipt["reservation"] is False
    assert receipt["availability_scope"] == "user_selected_calendars"
    date = (datetime.fromisoformat(receipt["anchor_at"]).date() + timedelta(days=1)).isoformat()
    assert receipt["slots"][0]["start"] == date + "T00:00:00Z"
    assert receipt["slots"][1]["start"] == date + "T00:30:00Z"
    query = json.loads(setup.calls[-1].content)
    assert datetime.fromisoformat(query["timeMin"]) == (
        datetime.fromisoformat(receipt["slots"][0]["start"]) - timedelta(minutes=20)
    )
    assert datetime.fromisoformat(query["timeMax"]) == (
        datetime.fromisoformat(receipt["slots"][0]["start"]) + timedelta(days=1, minutes=15)
    )
    count = len(setup.calls)
    replay = post(db_client, auth_headers, participant_timezones=["Australia/Melbourne"])
    assert replay.json() == receipt and len(setup.calls) == count
    assert post(db_client, auth_headers, count=2).status_code == 409
    route = "/calendar/slot-requests/" + receipt["id"]
    assert db_client.get(route, headers=auth_headers(1)).json() == receipt
    assert db_client.get(route, headers=auth_headers(2)).status_code == 404
    assert (
        db_client.get("/calendar/slot-requests/not-uuid", headers=auth_headers(1)).status_code
        == 422
    )


def test_ambiguous_context_correction_preserves_anchor(db_client, auth_headers, setup):
    first = post(db_client, auth_headers, at_time="4").json()
    assert first["state"] == "needs_clarification"
    assert first["resolution"]["reason"] == "ambiguous_meridiem" and setup.calls == []
    corrected = post(
        db_client,
        auth_headers,
        request_id="answer",
        at_time="4",
        time_context={"start_minute": 15 * 60, "end_minute": 18 * 60},
        anchor_from_request_id=first["id"],
    )
    assert corrected.status_code == 202, corrected.text
    second = corrected.json()
    assert second["anchor_at"] == first["anchor_at"] and second["anchor_source"] == "prior_request"
    assert len(second["slots"]) == 1 and "T16:00:00" in second["slots"][0]["start"]
    assert second["resolution"]["assumptions"][-1]["source"] == "explicit_time_context"


def test_other_users_anchor_cannot_be_reused(db_client, auth_headers, setup):
    first = post(db_client, auth_headers, at_time="4").json()
    asyncio.run(
        service.save_preferences(2, SavePreferences.model_validate(saved(calendar_ids=["other"])))
    )
    setup.calls.clear()
    response = db_client.post(
        "/calendar/slot-requests",
        headers=auth_headers(2),
        json=body(anchor_from_request_id=first["id"]),
    )
    assert response.status_code == 404 and setup.calls == []


@pytest.mark.parametrize("missing", [False, True])
def test_unknown_is_never_available(db_client, auth_headers, setup, missing):
    setup.missing, setup.unknown = missing, not missing
    response = post(db_client, auth_headers)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["state"] == "unknown" and receipt["slots"] == []
    assert receipt["reason"] == "calendar_coverage_unknown"


@pytest.mark.parametrize("change", ["preferences", "account", "scope"])
def test_changes_during_read_fence_publication(
    db_client, db_sessionmaker, auth_headers, setup, change
):
    async def mutate(req):
        if req.url.path.endswith("freeBusy"):
            async with db_sessionmaker() as session:
                if change == "preferences":
                    pref = await session.get(CalendarPreference, 1)
                    pref.version += 1
                else:
                    user = await session.get(User, 1)
                    if change == "scope":
                        user.google_scopes = []
                    else:
                        user.google_account_version += 1
                await session.commit()

    setup.callback = mutate
    result = post(db_client, auth_headers)
    assert result.status_code == (403 if change == "scope" else 409), result.text
    assert result.json()["error"]["detail"]["slot_request_id"]

    async def check():
        async with db_sessionmaker() as session:
            row = await session.scalar(select(CalendarSlotRequest))
            assert row.state == "failed" and row.evidence_id is None and row.result is None

    asyncio.run(check())


@pytest.mark.parametrize(
    "change", ["preferences", "account", "expiry", "evidence_expiry", "policy"]
)
def test_stored_offers_reject_stale_versions_and_expiry(
    db_client, db_sessionmaker, auth_headers, setup, change
):
    response = post(db_client, auth_headers)
    assert response.status_code == 202, response.text
    receipt = response.json()

    async def mutate():
        # ORM fixture intentionally lacks SQL migration triggers; migration tests cover them.
        async with db_sessionmaker() as session:
            row = await session.get(CalendarSlotRequest, receipt["id"])
            if change == "preferences":
                pref = await session.get(CalendarPreference, 1)
                pref.version += 1
            elif change == "account":
                user = await session.get(User, 1)
                user.google_account_version += 1
            elif change == "policy":
                row.policy_version = "retired"
            elif change == "evidence_expiry":
                evidence = await session.get(CalendarEvidence, row.evidence_id)
                evidence.checked_at -= timedelta(minutes=10)
                evidence.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            else:
                row.created_at -= timedelta(minutes=10)
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

    asyncio.run(mutate())
    assert (
        db_client.get(
            "/calendar/slot-requests/" + receipt["id"], headers=auth_headers(1)
        ).status_code
        == 409
    )
    assert post(db_client, auth_headers).status_code == 409


def test_concurrent_duplicate_is_one_lookup(db_sessionmaker, setup):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(req):
            if req.url.path.endswith("freeBusy"):
                entered.set()
                await release.wait()

        setup.callback, setup.check_idle = pause, False
        query = SlotRequest.model_validate(body())
        first = asyncio.create_task(slots.submit(1, query))
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = await slots.submit(1, query)
        assert duplicate.state == "processing"
        await setup.assert_no_transactions()
        release.set()
        result = await asyncio.wait_for(first, 5)
        assert result.id == duplicate.id and result.state == "complete"
        assert sum(req.url.path.endswith("freeBusy") for req in setup.calls) == 1

    asyncio.run(run())


def test_process_loss_leaves_expiring_receipt_and_preserves_original_anchor(
    db_client, db_sessionmaker, auth_headers, setup, monkeypatch
):
    real = service.query_freebusy

    async def crash(*args):
        raise RuntimeError("simulated process loss")

    monkeypatch.setattr(service, "query_freebusy", crash)
    assert post(db_client, auth_headers).status_code == 500
    replay = post(db_client, auth_headers).json()
    assert replay["state"] == "processing" and replay["slots"] == [] and setup.calls == []

    async def age():
        async with db_sessionmaker() as session:
            row = await session.get(CalendarSlotRequest, replay["id"])
            row.created_at -= timedelta(days=1)
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            # A request accepted yesterday just before midnight, followed up today.
            row.anchor_at = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(minutes=2)
            await session.commit()
            return row.anchor_at

    anchor = asyncio.run(age())
    assert post(db_client, auth_headers).status_code == 409
    monkeypatch.setattr(service, "query_freebusy", real)
    corrected = post(
        db_client, auth_headers, request_id="retry-new", anchor_from_request_id=replay["id"]
    )
    assert corrected.status_code == 202, corrected.text
    assert datetime.fromisoformat(corrected.json()["anchor_at"]) == anchor
    assert datetime.fromisoformat(corrected.json()["resolution"]["start"]).date() == (
        anchor.date() + timedelta(days=1)
    )


def test_finish_failure_rolls_back_and_returns_failed_receipt(
    db_client, db_sessionmaker, auth_headers, setup, monkeypatch
):
    original = slots.option

    def fail(*args):
        raise ApiError(409, "test_publication_failure", "Simulated rollback")

    monkeypatch.setattr(slots, "option", fail)
    response = post(db_client, auth_headers)
    assert response.status_code == 409
    receipt_id = response.json()["error"]["detail"]["slot_request_id"]
    receipt = db_client.get("/calendar/slot-requests/" + receipt_id, headers=auth_headers(1)).json()
    assert (
        receipt["state"] == "failed" and receipt["evidence_id"] is None and receipt["slots"] == []
    )
    monkeypatch.setattr(slots, "option", original)
    assert post(db_client, auth_headers).json() == receipt
    assert sum(req.url.path.endswith("freeBusy") for req in setup.calls) == 1


def test_bounded_or_elapsed_request_does_not_read_google(db_client, auth_headers, setup):
    response = post(db_client, auth_headers, date="9999-12-31")
    assert response.status_code == 202 and response.json()["state"] == "needs_clarification"
    past = post(db_client, auth_headers, request_id="past", date="2020-01-01")
    assert past.status_code == 202 and past.json()["reason"] == "window_elapsed"
    assert past.json()["slots"] == [] and setup.calls == []


def test_saved_working_hours_disambiguate_four_without_extra_question(
    db_client, auth_headers, setup
):
    values = saved(1)
    values["preferences"]["working_periods"] = [
        {"weekday": day, "start_minute": 9 * 60, "end_minute": 17 * 60} for day in range(7)
    ]
    asyncio.run(service.save_preferences(1, SavePreferences.model_validate(values)))
    response = post(db_client, auth_headers, expected_preferences_version=2, at_time="4")
    assert response.status_code == 202, response.text
    result = response.json()
    assert len(result["slots"]) == 1 and "T16:00:00" in result["slots"][0]["start"]
    assert result["resolution"]["assumptions"][-1]["source"] == "saved_working_hours"


def test_engine_refuses_wrong_owned_or_narrow_evidence(
    db_client, db_sessionmaker, auth_headers, setup, monkeypatch
):
    original = service.query_freebusy

    async def narrow(user_id, query):
        evidence = await original(user_id, query)
        async with db_sessionmaker() as session:
            row = await session.get(CalendarEvidence, evidence.id)
            result = dict(row.result)
            result["start"] = (query.start + timedelta(minutes=1)).isoformat()
            row.result = result
            await session.commit()
        return evidence

    monkeypatch.setattr(service, "query_freebusy", narrow)
    result = post(db_client, auth_headers)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "calendar_evidence_incomplete"

    # A saved, fully owned query is required even when an evidence ID is known.
    async def foreign():
        async with db_sessionmaker() as session:
            evidence = await session.scalar(select(CalendarEvidence))
            return evidence.id

    evidence_id = asyncio.run(foreign())
    with pytest.raises(ApiError) as error:
        asyncio.run(
            slots.finish(2, result.json()["error"]["detail"]["slot_request_id"], evidence_id)
        )
    assert error.value.status == 404


@pytest.mark.parametrize("date", ["0001-01-01", "9999-12-30", "9999-12-31"])
def test_extreme_dates_have_bounded_outcomes(db_client, auth_headers, setup, date):
    response = post(db_client, auth_headers, date=date)
    assert response.status_code == 202, response.text
    assert response.json()["state"] in {"complete", "needs_clarification"}
    assert response.json()["slots"] == [] and setup.calls == []


def test_get_rechecks_expiry_after_waiting_for_account_lock(db_sessionmaker, setup, monkeypatch):
    async def run():
        result = await slots.submit(1, SlotRequest.model_validate(body()))
        async with db_sessionmaker() as session:
            row = await session.get(CalendarSlotRequest, result.id)
            row.expires_at = datetime.now(UTC) + timedelta(seconds=0.3)
            deadline = row.expires_at
            await session.commit()
        entered = asyncio.Event()
        original = service.account

        async def observe(*args, **kwargs):
            entered.set()
            return await original(*args, **kwargs)

        monkeypatch.setattr(service, "account", observe)
        async with db_sessionmaker() as blocker:
            await blocker.get(User, 1, with_for_update=True)
            read = asyncio.create_task(slots.get_request(1, result.id))
            await asyncio.wait_for(entered.wait(), 3)
            # Wait beyond the saved deadline while the reader is waiting on our lock.
            await asyncio.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.05)
            await blocker.commit()
        with pytest.raises(ApiError) as error:
            await asyncio.wait_for(read, 3)
        assert error.value.code == "calendar_slots_expired"

    asyncio.run(run())
