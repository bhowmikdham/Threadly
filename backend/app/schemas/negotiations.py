"""Explicit user adoption/selection of stored offers; no free-text or booking command."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.calendar import StrictModel
from app.schemas.slots import SlotOption

POLICY = "meeting-negotiation-1.0.0"


class Keyed(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")


class CreateNegotiation(Keyed):
    thread_id: str = Field(min_length=1, max_length=32)
    expected_thread_version: int = Field(strict=True, ge=0)


class OfferRequest(Keyed):
    expected_version: int = Field(strict=True, ge=1)
    expected_thread_version: int = Field(strict=True, ge=0)
    slot_request_id: UUID


class SelectOffer(Keyed):
    expected_version: int = Field(strict=True, ge=1)
    offer_id: UUID
    slot_id: UUID


class CloseNegotiation(Keyed):
    expected_version: int = Field(strict=True, ge=1)


class OfferOut(StrictModel):
    id: str
    negotiation_id: str
    revision: int
    created_version: int
    current_version: int
    thread_version: int
    slot_request_id: str
    created_at: datetime
    expires_at: datetime
    slots: list[SlotOption]
    assumptions: list[dict]
    usable: bool
    blockers: list[str]
    invitee_availability: Literal["unknown"] = "unknown"
    reservation: Literal[False] = False


class SelectionOut(StrictModel):
    id: str
    negotiation_id: str
    offer_id: str
    created_version: int
    current_version: int
    state: Literal["checking", "selected", "conflict", "unknown", "failed", "superseded"]
    slot_id: str
    slot: SlotOption
    checked_slot_request_id: str | None
    created_at: datetime
    expires_at: datetime
    usable: bool
    blockers: list[str]
    error_code: str | None
    booking_approved: Literal[False] = False
    reservation: Literal[False] = False


class NegotiationOut(StrictModel):
    id: str
    thread_id: str
    version: int
    state: Literal["open", "offered", "checking", "selected", "closed"]
    policy_version: str
    created_at: datetime
    current_offer: OfferOut | None
    current_selection: SelectionOut | None
    booking_approved: Literal[False] = False
    event_created: Literal[False] = False
