from fastapi import APIRouter, Depends
from threadly_common.errors import APIError

from .. import clients, config, security

router = APIRouter()


@router.post("/sync")
async def trigger_sync(user_id: int = Depends(security.current_user)):
    """Manual sync for the current user (the worker also runs on an interval)."""
    resp = await clients.http.post(
        f"{config.GMAIL_SERVICE_URL}/internal/sync/trigger",
        json={"user_id": user_id},
        headers=security.internal_headers(user_id),
    )
    if resp.status_code != 200:
        raise APIError("sync_failed", "Sync failed.", status=502, details=resp.json())
    return resp.json()
