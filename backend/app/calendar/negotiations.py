"""Owned immutable offers and explicit selections. Never sends mail or creates events."""

from uuid import uuid4

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.calendar import service, slots
from app.db.engine import get_session_factory
from app.db.models import (
    CalendarPreference,
    CalendarSlotRequest,
    MeetingNegotiation,
    MeetingOffer,
    MeetingSelection,
    Thread,
    User,
)
from app.schemas.calendar import parse_instant
from app.schemas.negotiations import POLICY, NegotiationOut, OfferOut, SelectionOut
from app.schemas.slots import SlotRequest


def conflict(code="negotiation_changed"):
    return ApiError(409, code, "Meeting context changed; review current options and retry.")


def hashed(body):
    return digest(body.model_dump(mode="json"))


def replay(row, body):
    if row.request_hash != hashed(body):
        raise ApiError(409, "idempotency_conflict", "Request ID was used for different input.")


async def owned(session, model, owner, identifier, negotiation_id=None):
    query = select(model).where(model.id == identifier, model.user_id == owner)
    if negotiation_id is not None:
        query = query.where(model.negotiation_id == negotiation_id)
    row = await session.scalar(query)
    if row is None:
        raise ApiError(404, "meeting_record_missing", "Meeting record not found.")
    return row


async def locked(session, owner, identifier, *, require_calendar=True):
    neg = await owned(session, MeetingNegotiation, owner, identifier)
    # Same first locks as B12/B13; sync also serializes account before thread changes.
    if require_calendar:
        await service.account(session, owner, lock=True)
    elif await session.get(User, owner, with_for_update=True, populate_existing=True) is None:
        raise ApiError(401, "unauthorized", "Account no longer exists.")
    await session.get(CalendarPreference, owner, with_for_update=True)
    thread = await session.scalar(
        select(Thread).where(Thread.id == neg.thread_id, Thread.user_id == owner).with_for_update()
    )
    neg = await session.scalar(
        select(MeetingNegotiation)
        .where(MeetingNegotiation.id == identifier, MeetingNegotiation.user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if neg is None or thread is None:
        raise ApiError(404, "meeting_record_missing", "Meeting record not found.")
    return neg, thread


def writable(neg, expected):
    if neg.policy_version != POLICY or neg.version != expected or neg.state == "closed":
        raise conflict()


async def slot_query(session, owner, identifier):
    return await owned(session, CalendarSlotRequest, owner, identifier)


async def offer_blockers(session, neg, thread, offer):
    reasons = []
    if neg.policy_version != POLICY:
        reasons.append("negotiation_policy_changed")
    if neg.state == "closed":
        reasons.append("negotiation_closed")
    if neg.current_offer_id != offer.id:
        reasons.append("offer_superseded")
    if thread.version != offer.thread_version:
        reasons.append("thread_changed")
    query = await slot_query(session, neg.user_id, offer.slot_request_id)
    try:
        await slots.check_current(session, neg.user_id, query)
    except ApiError as error:
        reasons.append(error.code)
    now = await session.scalar(select(func.clock_timestamp()))
    if offer.expires_at <= now:
        reasons.append("offer_expired")
    return reasons, query


async def offer_view(session, neg, thread, offer):
    reasons, query = await offer_blockers(session, neg, thread, offer)
    if neg.state == "checking":
        reasons.append("selection_in_progress")
    return OfferOut(
        id=offer.id,
        negotiation_id=neg.id,
        revision=offer.revision,
        created_version=offer.created_version,
        current_version=neg.version,
        thread_version=offer.thread_version,
        slot_request_id=offer.slot_request_id,
        created_at=offer.created_at,
        expires_at=offer.expires_at,
        slots=query.result["slots"],
        assumptions=query.resolution["assumptions"],
        usable=not reasons,
        blockers=reasons,
    )


def chosen(query, slot_id):
    for item in (query.result or {}).get("slots", []):
        if item["id"] == slot_id:
            return item
    raise ApiError(422, "slot_not_offered", "Choose a slot ID from this exact offer.")


async def selection_view(session, neg, thread, selection):
    offer = await owned(session, MeetingOffer, neg.user_id, selection.offer_id, neg.id)
    reasons, query = await offer_blockers(session, neg, thread, offer)
    if neg.current_selection_id != selection.id:
        reasons.append("selection_superseded")
    if selection.state != "selected":
        reasons.append("selection_" + selection.state)
    if selection.checked_slot_request_id:
        checked = await slot_query(session, neg.user_id, selection.checked_slot_request_id)
        try:
            await slots.check_current(session, neg.user_id, checked)
        except ApiError as error:
            reasons.append(error.code)
    now = await session.scalar(select(func.clock_timestamp()))
    if selection.expires_at <= now:
        reasons.append("selection_expired")
    return SelectionOut(
        id=selection.id,
        negotiation_id=neg.id,
        offer_id=offer.id,
        created_version=selection.created_version,
        current_version=neg.version,
        state=selection.state,
        slot_id=selection.slot_id,
        slot=chosen(query, selection.slot_id),
        checked_slot_request_id=selection.checked_slot_request_id,
        created_at=selection.created_at,
        expires_at=selection.expires_at,
        usable=not reasons,
        blockers=list(dict.fromkeys(reasons)),
        error_code=selection.error_code,
    )


async def view(session, neg, thread):
    offer = (
        await owned(session, MeetingOffer, neg.user_id, neg.current_offer_id, neg.id)
        if neg.current_offer_id
        else None
    )
    selection = (
        await owned(session, MeetingSelection, neg.user_id, neg.current_selection_id, neg.id)
        if neg.current_selection_id
        else None
    )
    return NegotiationOut(
        id=neg.id,
        thread_id=thread.gmail_thread_id,
        version=neg.version,
        state=neg.state,
        policy_version=neg.policy_version,
        created_at=neg.created_at,
        current_offer=await offer_view(session, neg, thread, offer) if offer else None,
        current_selection=await selection_view(session, neg, thread, selection)
        if selection
        else None,
    )


async def create(owner, body):
    async with get_session_factory()() as session:
        await service.account(session, owner, lock=True)
        existing = await session.scalar(
            select(MeetingNegotiation).where(
                MeetingNegotiation.user_id == owner,
                MeetingNegotiation.request_id == body.request_id,
            )
        )
        if existing:
            replay(existing, body)
            neg, thread = await locked(session, owner, existing.id)
            return await view(session, neg, thread)
        thread = await session.scalar(
            select(Thread)
            .where(Thread.user_id == owner, Thread.gmail_thread_id == body.thread_id)
            .with_for_update()
        )
        if thread is None:
            raise ApiError(404, "thread_not_found", "Select an owned synced thread.")
        if thread.version != body.expected_thread_version or thread.last_msg_id is None:
            raise conflict("thread_changed")
        neg = MeetingNegotiation(
            id=str(uuid4()),
            user_id=owner,
            thread_id=thread.id,
            request_id=body.request_id,
            request_hash=hashed(body),
            policy_version=POLICY,
            version=1,
            state="open",
        )
        session.add(neg)
        await session.flush()
        result = await view(session, neg, thread)
        await session.commit()
        return result


async def get(owner, identifier, *, offer_id=None, selection_id=None):
    async with get_session_factory()() as session:
        neg, thread = await locked(session, owner, identifier)
        if offer_id:
            offer = await owned(session, MeetingOffer, owner, offer_id, neg.id)
            return await offer_view(session, neg, thread, offer)
        if selection_id:
            selection = await owned(session, MeetingSelection, owner, selection_id, neg.id)
            return await selection_view(session, neg, thread, selection)
        return await view(session, neg, thread)


async def offer(owner, identifier, body):
    async with get_session_factory()() as session:
        neg, thread = await locked(session, owner, identifier)
        existing = await session.scalar(
            select(MeetingOffer).where(
                MeetingOffer.negotiation_id == neg.id, MeetingOffer.request_id == body.request_id
            )
        )
        if existing:
            replay(existing, body)
            return await offer_view(session, neg, thread, existing)
        writable(neg, body.expected_version)
        if thread.version != body.expected_thread_version or thread.last_msg_id is None:
            raise conflict("thread_changed")
        query = await slot_query(session, owner, str(body.slot_request_id))
        await slots.check_current(session, owner, query)
        if query.created_at < thread.updated_at:
            raise conflict("slot_query_precedes_thread")
        if query.state != "complete" or not (query.result or {}).get("slots"):
            raise conflict("no_verified_slots")
        prior = (
            await owned(session, MeetingOffer, owner, neg.current_offer_id, neg.id)
            if neg.current_offer_id
            else None
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if query.expires_at <= now:
            raise conflict("offer_expired")
        row = MeetingOffer(
            id=str(uuid4()),
            negotiation_id=neg.id,
            user_id=owner,
            request_id=body.request_id,
            request_hash=hashed(body),
            revision=prior.revision + 1 if prior else 1,
            created_version=neg.version + 1,
            thread_version=thread.version,
            slot_request_id=query.id,
            created_at=now,
            expires_at=query.expires_at,
        )
        session.add(row)
        await session.flush()
        neg.version += 1
        neg.state, neg.current_offer_id, neg.current_selection_id = "offered", row.id, None
        await session.flush()
        result = await offer_view(session, neg, thread, row)
        await session.commit()
        return result


async def select_slot(owner, identifier, body):
    async with get_session_factory()() as session:
        neg, thread = await locked(session, owner, identifier)
        existing = await session.scalar(
            select(MeetingSelection).where(
                MeetingSelection.negotiation_id == neg.id,
                MeetingSelection.request_id == body.request_id,
            )
        )
        if existing:
            replay(existing, body)
            return await selection_view(session, neg, thread, existing)
        writable(neg, body.expected_version)
        if neg.state == "checking":
            raise conflict("selection_in_progress")
        selected_offer = await owned(session, MeetingOffer, owner, str(body.offer_id), neg.id)
        reasons, query = await offer_blockers(session, neg, thread, selected_offer)
        if reasons:
            raise conflict(reasons[0])
        selected = chosen(query, str(body.slot_id))
        now = await session.scalar(select(func.clock_timestamp()))
        if selected_offer.expires_at <= now:
            raise conflict("offer_expired")
        receipt = MeetingSelection(
            id=str(uuid4()),
            negotiation_id=neg.id,
            user_id=owner,
            offer_id=selected_offer.id,
            request_id=body.request_id,
            request_hash=hashed(body),
            created_version=neg.version + 1,
            slot_id=str(body.slot_id),
            state="checking",
            created_at=now,
            expires_at=selected_offer.expires_at,
        )
        session.add(receipt)
        await session.flush()
        neg.version += 1
        neg.state, neg.current_selection_id = "checking", receipt.id
        # Exact UTC clock check avoids reinterpreting local fold/AM-PM or selecting an alternative.
        check = recheck_request(receipt, query, selected)
        receipt_id = receipt.id
        await session.commit()
    # Only read calls occur here, after durable reservation and outside all DB transactions.
    error_code, checked_id = None, None
    try:
        checked = await slots.submit(owner, check)
        checked_id = checked.id
    except ApiError as error:
        error_code = error.code
    try:
        return await finish(owner, identifier, receipt_id, checked_id, error_code)
    except ApiError as error:
        # Reconnection/deletion can prevent finalization. Preserve the receipt ID;
        # do not disguise an authorization failure as a successful selection.
        raise ApiError(
            error.status, error.code, error.message, {"selection_id": receipt_id}
        ) from None


def recheck_request(receipt, query, selected):
    start, end = parse_instant(selected["start"]), parse_instant(selected["end"])
    return SlotRequest(
        request_id="meeting-selection-" + receipt.id,
        expected_preferences_version=query.preferences_version,
        date=start.date().isoformat(),
        at_time=start.strftime("%H:%M"),
        timezone="UTC",
        duration_minutes=int((end - start).total_seconds() / 60),
        count=1,
        anchor_from_request_id=query.id,
    )


async def finish(owner, identifier, receipt_id, checked_id, error_code):
    async with get_session_factory()() as session:
        neg, thread = await locked(session, owner, identifier)
        receipt = await owned(session, MeetingSelection, owner, receipt_id, neg.id)
        if receipt.state != "checking":
            return await selection_view(session, neg, thread, receipt)
        selected_offer = await owned(session, MeetingOffer, owner, receipt.offer_id, neg.id)
        reasons, query = await offer_blockers(session, neg, thread, selected_offer)
        current = (
            neg.version == receipt.created_version
            and neg.current_selection_id == receipt.id
            and neg.state == "checking"
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if not current:
            receipt.state, receipt.error_code = "superseded", "negotiation_changed"
        elif reasons or receipt.expires_at <= now:
            receipt.state, receipt.error_code = (
                "failed",
                reasons[0] if reasons else "selection_expired",
            )
        elif error_code:
            receipt.state, receipt.error_code = "failed", error_code
        else:
            checked = await slot_query(session, owner, checked_id)
            selected = chosen(query, receipt.slot_id)
            if checked.request != recheck_request(receipt, query, selected).model_dump(mode="json"):
                raise conflict("slot_recheck_mismatch")
            try:
                await slots.check_current(session, owner, checked)
            except ApiError as error:
                receipt.state, receipt.error_code = "failed", error.code
            else:
                receipt.checked_slot_request_id = checked.id
                receipt.expires_at = min(receipt.expires_at, checked.expires_at)
                selected = chosen(query, receipt.slot_id)
                checked_slots = (checked.result or {}).get("slots", [])
                same = len(checked_slots) == 1 and all(
                    parse_instant(checked_slots[0][key]) == parse_instant(selected[key])
                    for key in ("start", "end")
                )
                if checked.state == "unknown":
                    receipt.state = "unknown"
                elif checked.state == "complete":
                    receipt.state = "selected" if same else "conflict"
                else:
                    receipt.state, receipt.error_code = "failed", "slot_recheck_incomplete"
        if current:
            neg.version += 1
            neg.state = "selected" if receipt.state == "selected" else "offered"
        await session.flush()
        result = await selection_view(session, neg, thread, receipt)
        await session.commit()
        return result


async def close(owner, identifier, body):
    async with get_session_factory()() as session:
        # Closing is a local owner decision, even after provider access is revoked.
        neg, thread = await locked(session, owner, identifier, require_calendar=False)
        if neg.state == "closed" and neg.close_request_id == body.request_id:
            if neg.close_request_hash != hashed(body):
                raise ApiError(409, "idempotency_conflict", "Close key used for different input.")
            return await view(session, neg, thread)
        writable(neg, body.expected_version)
        neg.state, neg.version = "closed", neg.version + 1
        neg.close_request_id, neg.close_request_hash = body.request_id, hashed(body)
        result = await view(session, neg, thread)
        await session.commit()
        return result
