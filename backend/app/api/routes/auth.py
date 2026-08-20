"""Auth routes — thin HTTP shell over app.auth.service (module 2). Shapes: docs/api-contract.md."""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.post("/google/exchange")
async def google_exchange() -> dict:
    # W1: body {"code"} -> auth.service.exchange_code() -> {"jwt", "user"}
    raise not_implemented("OAuth code exchange", "W1")


@router.post("/refresh")
async def refresh(user_id: CurrentUser) -> dict:
    # W1: re-issue session JWT for a still-valid user
    raise not_implemented("Session refresh", "W1")
