"""Authenticated Calendar read API. No model-selected IDs and no event writes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser
from app.api.routes.auth import no_store
from app.calendar import service
from app.schemas.calendar import (
    CalendarListOut,
    FreeBusyOut,
    FreeBusyRequest,
    PreferencesOut,
    SavePreferences,
)

router = APIRouter(dependencies=[Depends(no_store)])


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
