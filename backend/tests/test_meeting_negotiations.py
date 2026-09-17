"""Meeting API lifecycle across real PostgreSQL and mocked Google reads."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import client, negotiations, service, slots
from app.db.models import (
    AssistantAction,
    CalendarPreference,
    CalendarSlotRequest,
    MeetingNegotiation,
    MeetingOffer,
    MeetingSelection,
    Thread,
)
from app.schemas.calendar import CalendarCoverage, SavePreferences
from app.schemas.negotiations import CloseNegotiation, OfferRequest, SelectOffer
from app.schemas.slots import SlotRequest
from tests.conftest import needs_pg
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_slot_service import body as slot_body
from tests.test_slot_service import saved

pytestmark = needs_pg


@pytest.fixture()
def setup(calendar_setup, db_sessionmaker, monkeypatch):  # noqa: F811
    monkeypatch.setattr(slots, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(negotiations, "get_session_factory", lambda: db_sessionmaker)

    async def seed():
        await service.save_preferences(1, SavePreferences.model_validate(saved()))
        await service.save_preferences(
            2, SavePreferences.model_validate(saved(calendar_ids=["other"]))
        )
        async with db_sessionmaker() as session:
            for uid in (1, 2):
                session.add(
                    Thread(
                        id=uid,
                        user_id=uid,
                        gmail_thread_id=f"meeting-{uid}",
                        version=1,
                        last_msg_id=f"mail-{uid}",
                    )
                )
            await session.commit()

    asyncio.run(seed())
    calendar_setup.calls.clear()
    return calendar_setup


def create_body(**changes):
    return {
        "request_id": "neg-1",
        "thread_id": "meeting-1",
        "expected_thread_version": 1,
        **changes,
    }


def create(db_client, auth_headers, **changes):
    response = db_client.post(
        "/calendar/negotiations", headers=auth_headers(1), json=create_body(**changes)
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_offer(db_client, auth_headers):
    neg = create(db_client, auth_headers)
    query = db_client.post(
        "/calendar/slot-requests", headers=auth_headers(1), json=slot_body()
    ).json()
    body = {
        "request_id": "offer-1",
        "expected_version": 1,
        "expected_thread_version": 1,
        "slot_request_id": query["id"],
    }
    response = db_client.post(
        f"/calendar/negotiations/{neg['id']}/offers", headers=auth_headers(1), json=body
    )
    assert response.status_code == 201, response.text
    return neg, response.json(), body


def selection_body(offer, **changes):
    return {
        "request_id": "select-1",
        "expected_version": offer["current_version"],
        "offer_id": offer["id"],
        "slot_id": offer["slots"][1]["id"],
        **changes,
    }


def select_slot(db_client, auth_headers, neg, offer, **changes):
    return db_client.post(
        f"/calendar/negotiations/{neg['id']}/selections",
        headers=auth_headers(1),
        json=selection_body(offer, **changes),
    )


def test_offer_and_exact_second_slot_selection_never_books(
    db_client, db_sessionmaker, auth_headers, setup
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    assert offer["usable"] and len(offer["slots"]) == 3 and offer["revision"] == 1
    assert offer["invitee_availability"] == "unknown" and offer["reservation"] is False
    before = len(setup.calls)
    response = select_slot(db_client, auth_headers, neg, offer)
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["state"] == "selected" and result["usable"]
    assert result["slot"] == offer["slots"][1] and result["booking_approved"] is False
    assert result["checked_slot_request_id"] != offer["slot_request_id"]
    assert len(setup.calls) == before + 2  # Current ACL list AND a fresh exact freebusy read.
    assert response.headers["cache-control"] == "no-store"
    assert select_slot(db_client, auth_headers, neg, offer).json() == result
    assert len(setup.calls) == before + 2
    current = db_client.get(f"/calendar/negotiations/{neg['id']}", headers=auth_headers(1)).json()
    assert current["version"] == 4 and current["state"] == "selected"
    assert current["event_created"] is False

    async def count():
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0
            assert await session.scalar(select(func.count()).select_from(MeetingSelection)) == 1

    asyncio.run(count())


def test_idempotency_and_owner_checks(db_client, auth_headers, setup):
    assert db_client.post("/calendar/negotiations", json=create_body()).status_code == 401
    neg, offer, offer_body = make_offer(db_client, auth_headers)
    assert create(db_client, auth_headers)["id"] == neg["id"]
    assert (
        db_client.post(
            "/calendar/negotiations",
            headers=auth_headers(1),
            json=create_body(expected_thread_version=2),
        ).status_code
        == 409
    )
    path = f"/calendar/negotiations/{neg['id']}"
    assert db_client.get(path, headers=auth_headers(2)).status_code == 404
    assert (
        db_client.get(path + f"/offers/{offer['id']}", headers=auth_headers(2)).status_code == 404
    )
    assert (
        db_client.post(path + "/offers", headers=auth_headers(1), json=offer_body).json() == offer
    )
    assert (
        db_client.post(
            path + "/offers", headers=auth_headers(1), json={**offer_body, "expected_version": 2}
        ).status_code
        == 409
    )
    assert (
        db_client.post(
            "/calendar/negotiations", headers=auth_headers(2), json=create_body()
        ).status_code
        == 404
    )
    assert select_slot(db_client, auth_headers, neg, offer, slot_id=str(uuid4())).status_code == 422
    assert (
        select_slot(db_client, auth_headers, neg, offer, offer_id=str(uuid4())).status_code == 404
    )
    other = create(db_client, auth_headers, request_id="separate")
    assert (
        db_client.post(
            f"/calendar/negotiations/{other['id']}/selections",
            headers=auth_headers(1),
            json=selection_body(offer, expected_version=1),
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"start": "2030-01-01"},
        {"attendees_available": True},
        {"booking_approved": True},
        {"instruction": "yes book it"},
    ],
)
def test_untrusted_times_prose_or_approval_fields_cannot_select(
    db_client, auth_headers, setup, extra
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    response = select_slot(db_client, auth_headers, neg, offer, **extra)
    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["unknown", "conflict", "error"])
def test_recheck_unknown_conflict_or_failure_never_substitutes(
    db_client, auth_headers, setup, monkeypatch, kind
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    picked = offer["slots"][1]
    original = client.freebusy

    async def unavailable(token, ids, start, end):
        if kind == "error":
            raise ApiError(502, "calendar_provider_error", "Provider unavailable")
        if kind == "conflict":
            return [
                CalendarCoverage(
                    calendar_id=ids[0],
                    status="known",
                    busy=[{"start": picked["start"], "end": picked["end"]}],
                )
            ]
        return await original(token, ids, start, end)

    setup.unknown = kind == "unknown"
    monkeypatch.setattr(client, "freebusy", unavailable)
    response = select_slot(db_client, auth_headers, neg, offer)
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["state"] == ("failed" if kind == "error" else kind)
    assert not result["usable"] and result["slot"] == picked and not result["booking_approved"]


@pytest.mark.parametrize("change", ["thread", "preferences", "expired"])
def test_stale_offer_is_visible_but_cannot_be_selected(
    db_client, db_sessionmaker, auth_headers, setup, change
):
    neg, offer, _ = make_offer(db_client, auth_headers)

    async def mutate():
        async with db_sessionmaker() as session:
            if change == "thread":
                row = await session.get(Thread, 1)
                row.version += 1
                row.last_msg_id = "incoming-yes"
            elif change == "preferences":
                row = await session.get(CalendarPreference, 1)
                row.version += 1
            else:
                row = await session.get(MeetingOffer, offer["id"])
                row.created_at -= timedelta(minutes=10)
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

    asyncio.run(mutate())
    before = len(setup.calls)
    result = db_client.get(f"/calendar/negotiations/{neg['id']}", headers=auth_headers(1)).json()
    assert (
        not result["current_offer"]["usable"] and result["current_offer"]["slots"] == offer["slots"]
    )
    assert select_slot(db_client, auth_headers, neg, offer).status_code == 409
    assert len(setup.calls) == before


@pytest.mark.parametrize("change", ["thread", "preferences", "close", "new_offer"])
def test_inflight_changes_fence_selection(db_client, db_sessionmaker, auth_headers, setup, change):
    neg, offer, _ = make_offer(db_client, auth_headers)

    async def mutate(req):
        if not req.url.path.endswith("freeBusy"):
            return
        if change == "close":
            await negotiations.close(
                1, neg["id"], CloseNegotiation(request_id="stop", expected_version=3)
            )
        elif change == "new_offer":
            await negotiations.offer(
                1,
                neg["id"],
                OfferRequest(
                    request_id="replacement",
                    expected_version=3,
                    expected_thread_version=1,
                    slot_request_id=offer["slot_request_id"],
                ),
            )
        else:
            async with db_sessionmaker() as session:
                row = await session.get(Thread if change == "thread" else CalendarPreference, 1)
                row.version += 1
                await session.commit()

    setup.callback = mutate
    response = select_slot(db_client, auth_headers, neg, offer)
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["state"] == ("superseded" if change in {"close", "new_offer"} else "failed")
    assert not result["usable"] and not result["booking_approved"]
    current = db_client.get(f"/calendar/negotiations/{neg['id']}", headers=auth_headers(1)).json()
    if change == "close":
        assert current["state"] == "closed"
    if change == "new_offer":
        assert current["current_offer"]["revision"] == 2 and current["current_selection"] is None


def test_concurrent_same_selection_reads_google_once(db_client, auth_headers, setup):
    neg, offer, _ = make_offer(db_client, auth_headers)
    setup.calls.clear()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(req):
            if req.url.path.endswith("freeBusy"):
                entered.set()
                await release.wait()

        setup.callback, setup.check_idle = pause, False
        body = SelectOffer.model_validate(selection_body(offer))
        first = asyncio.create_task(negotiations.select_slot(1, neg["id"], body))
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = await negotiations.select_slot(1, neg["id"], body)
        assert duplicate.state == "checking" and not duplicate.usable
        current = await negotiations.get(1, neg["id"])
        assert not current.current_offer.usable
        assert "selection_in_progress" in current.current_offer.blockers
        with pytest.raises(ApiError):
            await negotiations.select_slot(
                1,
                neg["id"],
                body.model_copy(update={"request_id": "competing", "expected_version": 3}),
            )
        await setup.assert_no_transactions()
        release.set()
        final = await asyncio.wait_for(first, 5)
        assert final.id == duplicate.id and final.state == "selected"
        assert sum(r.url.path.endswith("freeBusy") for r in setup.calls) == 1

    asyncio.run(run())


def test_close_and_historical_offer_replay_do_not_reopen(db_client, auth_headers, setup):
    neg, offer, offer_body = make_offer(db_client, auth_headers)
    route = f"/calendar/negotiations/{neg['id']}"
    body = {"request_id": "close", "expected_version": 2}
    response = db_client.post(route + "/close", headers=auth_headers(1), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "closed"
    assert (
        db_client.post(route + "/close", headers=auth_headers(1), json=body).json()
        == response.json()
    )
    assert (
        db_client.post(
            route + "/close", headers=auth_headers(1), json={**body, "expected_version": 3}
        ).status_code
        == 409
    )
    history = db_client.post(route + "/offers", headers=auth_headers(1), json=offer_body).json()
    assert history["id"] == offer["id"] and not history["usable"]
    assert select_slot(db_client, auth_headers, neg, offer, expected_version=3).status_code == 409


def test_incomplete_and_foreign_slot_queries_cannot_be_offered(db_client, auth_headers, setup):
    neg = create(db_client, auth_headers)
    query = asyncio.run(slots.submit(1, SlotRequest.model_validate(slot_body(at_time="4"))))
    body = {
        "request_id": "offer",
        "expected_version": 1,
        "expected_thread_version": 1,
        "slot_request_id": query.id,
    }
    route = f"/calendar/negotiations/{neg['id']}/offers"
    response = db_client.post(route, headers=auth_headers(1), json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_verified_slots"
    query = asyncio.run(slots.submit(2, SlotRequest.model_validate(slot_body())))
    response = db_client.post(
        route, headers=auth_headers(1), json={**body, "slot_request_id": query.id}
    )
    assert response.status_code == 404


def test_new_reply_requires_new_slot_query_before_reoffering(
    db_client, db_sessionmaker, auth_headers, setup
):
    neg, offer, _ = make_offer(db_client, auth_headers)

    async def reply():
        async with db_sessionmaker() as session:
            thread = await session.get(Thread, 1)
            thread.version += 1
            thread.last_msg_id = "yes-new-message"
            await session.commit()

    asyncio.run(reply())
    path = f"/calendar/negotiations/{neg['id']}/offers"
    body = {
        "request_id": "replace",
        "expected_version": 2,
        "expected_thread_version": 2,
        "slot_request_id": offer["slot_request_id"],
    }
    result = db_client.post(path, headers=auth_headers(1), json=body)
    assert (
        result.status_code == 409 and result.json()["error"]["code"] == "slot_query_precedes_thread"
    )
    fresh = db_client.post(
        "/calendar/slot-requests", headers=auth_headers(1), json=slot_body(request_id="after-reply")
    ).json()
    result = db_client.post(
        path, headers=auth_headers(1), json={**body, "slot_request_id": fresh["id"]}
    )
    assert result.status_code == 201 and result.json()["revision"] == 2 and result.json()["usable"]
    old = db_client.get(path + "/" + offer["id"], headers=auth_headers(1)).json()
    assert "offer_superseded" in old["blockers"] and "thread_changed" in old["blockers"]
    assert not old["usable"]


def test_process_loss_does_not_repeat_recheck_and_explicit_offer_can_recover(
    db_client, auth_headers, setup, monkeypatch
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    original = slots.submit

    async def crash(*args):
        raise RuntimeError("simulated process loss")

    monkeypatch.setattr(slots, "submit", crash)
    before = len(setup.calls)
    assert select_slot(db_client, auth_headers, neg, offer).status_code == 500
    replay = select_slot(db_client, auth_headers, neg, offer)
    assert replay.status_code == 202 and replay.json()["state"] == "checking"
    assert not replay.json()["usable"] and len(setup.calls) == before
    monkeypatch.setattr(slots, "submit", original)
    path = f"/calendar/negotiations/{neg['id']}/offers"
    result = db_client.post(
        path,
        headers=auth_headers(1),
        json={
            "request_id": "recover",
            "expected_version": 3,
            "expected_thread_version": 1,
            "slot_request_id": offer["slot_request_id"],
        },
    )
    assert result.status_code == 201, result.text
    old = select_slot(db_client, auth_headers, neg, offer).json()
    assert "selection_superseded" in old["blockers"] and not old["usable"]


def test_offer_or_selection_creation_failure_rolls_back(
    db_client, db_sessionmaker, auth_headers, setup, monkeypatch
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    original = negotiations.recheck_request

    def fail(*args):
        raise ApiError(409, "fixture_rollback", "Simulated validation failure")

    monkeypatch.setattr(negotiations, "recheck_request", fail)
    result = select_slot(db_client, auth_headers, neg, offer)
    assert result.status_code == 409

    async def verify():
        async with db_sessionmaker() as session:
            row = await session.get(MeetingNegotiation, neg["id"])
            assert row.version == 2 and row.state == "offered" and row.current_selection_id is None
            assert await session.scalar(select(func.count()).select_from(MeetingSelection)) == 0

    asyncio.run(verify())
    monkeypatch.setattr(negotiations, "recheck_request", original)
    assert select_slot(db_client, auth_headers, neg, offer).json()["state"] == "selected"


def test_selected_receipt_becomes_unusable_when_original_query_expires(
    db_client, db_sessionmaker, auth_headers, setup
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    result = select_slot(db_client, auth_headers, neg, offer).json()

    async def expire():
        async with db_sessionmaker() as session:
            row = await session.get(CalendarSlotRequest, offer["slot_request_id"])
            row.created_at -= timedelta(minutes=10)
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

    asyncio.run(expire())
    path = f"/calendar/negotiations/{neg['id']}/selections/{result['id']}"
    response = db_client.get(path, headers=auth_headers(1))
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "selected" and not response.json()["usable"]
    assert db_client.get(path, headers=auth_headers(2)).status_code == 404


def test_sync_source_time_is_update_time_not_old_transaction_start(db_sessionmaker, setup):
    from app.db.repositories import apply_mailbox_changes

    async def run():
        async with db_sessionmaker() as session:
            transaction_start = await session.scalar(select(func.now()))
            marker = await session.scalar(select(func.clock_timestamp()))
            await apply_mailbox_changes(
                session,
                1,
                [
                    {
                        "gmail_thread_id": "meeting-1",
                        "gmail_msg_id": "new-reply",
                        "body_clean": "Yes, the second option.",
                        "is_from_user": False,
                        "sent_at": datetime.now(UTC),
                    }
                ],
                set(),
            )
            row = await session.get(Thread, 1)
            assert row.version == 2 and row.updated_at >= marker > transaction_start
            await session.commit()

    asyncio.run(run())


def test_failed_recheck_binding_cannot_publish_a_different_query(
    db_client, auth_headers, setup, monkeypatch
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    prior = asyncio.run(slots.get_request(1, offer["slot_request_id"]))

    async def unrelated(*args):
        return prior

    monkeypatch.setattr(slots, "submit", unrelated)
    response = select_slot(db_client, auth_headers, neg, offer)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "slot_recheck_mismatch"
    assert response.json()["error"]["detail"]["selection_id"]
    current = db_client.get(f"/calendar/negotiations/{neg['id']}", headers=auth_headers(1)).json()
    assert current["state"] == "checking" and not current["current_selection"]["usable"]


def test_competing_offer_edits_have_one_version_winner(
    db_client, db_sessionmaker, auth_headers, setup
):
    neg, offer, _ = make_offer(db_client, auth_headers)

    async def run():
        def body(key):
            return OfferRequest(
                request_id=key,
                expected_version=2,
                expected_thread_version=1,
                slot_request_id=offer["slot_request_id"],
            )

        results = await asyncio.gather(
            negotiations.offer(1, neg["id"], body("edit-a")),
            negotiations.offer(1, neg["id"], body("edit-b")),
            return_exceptions=True,
        )
        assert sum(isinstance(item, ApiError) and item.status == 409 for item in results) == 1
        winner = next(item for item in results if not isinstance(item, Exception))
        assert winner.revision == 2 and winner.current_version == 3
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(MeetingOffer)) == 2

    asyncio.run(run())


def test_publication_failure_leaves_checking_receipt_without_partial_selection(
    db_client, db_sessionmaker, auth_headers, setup, monkeypatch
):
    neg, offer, _ = make_offer(db_client, auth_headers)
    original = negotiations.selection_view

    async def fail(session, negotiation, thread, receipt):
        if receipt.state == "selected":
            raise ApiError(409, "fixture_publish_failure", "Simulated response validation failure")
        return await original(session, negotiation, thread, receipt)

    monkeypatch.setattr(negotiations, "selection_view", fail)
    result = select_slot(db_client, auth_headers, neg, offer)
    assert result.status_code == 409

    async def verify():
        async with db_sessionmaker() as session:
            row = await session.get(MeetingNegotiation, neg["id"])
            receipt = await session.get(MeetingSelection, row.current_selection_id)
            assert row.state == "checking" and row.version == 3
            assert receipt.state == "checking" and receipt.checked_slot_request_id is None

    asyncio.run(verify())
    monkeypatch.setattr(negotiations, "selection_view", original)
    assert select_slot(db_client, auth_headers, neg, offer).json()["state"] == "checking"
