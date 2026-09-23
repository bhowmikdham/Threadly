"""Authenticated Calendar read API. No model-selected IDs and no event writes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser
from app.api.routes.auth import no_store
from app.calendar import negotiations, service, slots
from app.mail.dependency import gmail_sources
from app.schemas.calendar import (
    CalendarListOut,
    FreeBusyOut,
    FreeBusyRequest,
    PreferencesOut,
    SavePreferences,
)
from app.schemas.negotiations import (
    CloseNegotiation,
    CreateNegotiation,
    NegotiationOut,
    OfferOut,
    OfferRequest,
    SelectionOut,
    SelectOffer,
)
from app.schemas.slots import SlotRequest, SlotRequestOut

router = APIRouter(dependencies=[Depends(no_store), Depends(gmail_sources)])


@router.get("/calendars", response_model=CalendarListOut)
async def list_calendars(user_id: CurrentUser):
    return await service.list_calendars(user_id)


@router.get("/preferences", response_model=PreferencesOut)
async def get_preferences(user_id: CurrentUser):
    return await service.get_preferences(user_id)


@router.put("/preferences", response_model=PreferencesOut)
async def save_preferences(body: SavePreferences, user_id: CurrentUser):
    return await service.save_preferences(user_id, body)


@router.post("/freebusy", response_model=FreeBusyOut, status_code=201)
async def query_freebusy(body: FreeBusyRequest, user_id: CurrentUser):
    return await service.query_freebusy(user_id, body)


@router.get("/freebusy/{evidence_id}", response_model=FreeBusyOut)
async def get_evidence(evidence_id: UUID, user_id: CurrentUser):
    return await service.get_evidence(user_id, str(evidence_id))


@router.post("/slot-requests", response_model=SlotRequestOut, status_code=202)
async def request_slots(body: SlotRequest, user_id: CurrentUser):
    return await slots.submit(user_id, body)


@router.get("/slot-requests/{request_id}", response_model=SlotRequestOut)
async def get_slots(request_id: UUID, user_id: CurrentUser):
    return await slots.get_request(user_id, str(request_id))


@router.post("/negotiations", response_model=NegotiationOut, status_code=201)
async def create_negotiation(body: CreateNegotiation, user_id: CurrentUser):
    return await negotiations.create(user_id, body)


@router.get("/negotiations/{negotiation_id}", response_model=NegotiationOut)
async def get_negotiation(negotiation_id: UUID, user_id: CurrentUser):
    return await negotiations.get(user_id, str(negotiation_id))


@router.post("/negotiations/{negotiation_id}/offers", response_model=OfferOut, status_code=201)
async def create_offer(negotiation_id: UUID, body: OfferRequest, user_id: CurrentUser):
    return await negotiations.offer(user_id, str(negotiation_id), body)


@router.get("/negotiations/{negotiation_id}/offers/{offer_id}", response_model=OfferOut)
async def get_offer(negotiation_id: UUID, offer_id: UUID, user_id: CurrentUser):
    return await negotiations.get(user_id, str(negotiation_id), offer_id=str(offer_id))


@router.post(
    "/negotiations/{negotiation_id}/selections", response_model=SelectionOut, status_code=202
)
async def select_offer(negotiation_id: UUID, body: SelectOffer, user_id: CurrentUser):
    return await negotiations.select_slot(user_id, str(negotiation_id), body)


@router.get("/negotiations/{negotiation_id}/selections/{selection_id}", response_model=SelectionOut)
async def get_selection(negotiation_id: UUID, selection_id: UUID, user_id: CurrentUser):
    return await negotiations.get(user_id, str(negotiation_id), selection_id=str(selection_id))


@router.post("/negotiations/{negotiation_id}/close", response_model=NegotiationOut)
async def close_negotiation(negotiation_id: UUID, body: CloseNegotiation, user_id: CurrentUser):
    return await negotiations.close(user_id, str(negotiation_id), body)
