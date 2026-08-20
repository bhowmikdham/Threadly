"""Drafting routes — orchestrator DRAFT intent: RAG + 4b (module 5 + 7 + 8)."""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.post("")
async def create_draft(user_id: CurrentUser):
    # W3: body {"thread_id", "instruction", "tone"} -> SSE stream of the draft
    raise not_implemented("Draft generation", "W3")


@router.post("/{draft_id}/send")
async def send_draft(draft_id: int, user_id: CurrentUser) -> dict:
    # W3: only after explicit user approval in the UI (human-in-the-loop, FR)
    raise not_implemented("Draft send", "W3")
