"""Entities + commitments — straight from postgres, NO model call (ADR 002)."""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.get("/entities")
async def list_entities(user_id: CurrentUser, type: str | None = None) -> dict:
    # W2: SELECT from entities (UNIQUE user,type,key) — provenance via source_msg_id
    raise not_implemented("Entity listing", "W2")


@router.get("/commitments")
async def list_commitments(user_id: CurrentUser, status: str = "open") -> dict:
    # W2: cross-thread commitment tracker (objective 1 in the proposal)
    raise not_implemented("Commitment listing", "W2")
