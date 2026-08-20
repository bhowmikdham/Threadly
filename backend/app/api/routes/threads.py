"""Thread listing/detail — served from postgres, no model calls. Shapes: docs/api-contract.md."""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.get("")
async def list_threads(user_id: CurrentUser, filter: str = "all", page: int = 1) -> dict:
    # W2: query threads table; filter=needs_reply uses classifier output stored at sync time
    raise not_implemented("Thread listing", "W2")


@router.get("/{thread_id}")
async def get_thread(thread_id: str, user_id: CurrentUser) -> dict:
    raise not_implemented("Thread detail", "W2")
