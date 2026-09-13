"""Read-only intent preview. Durable assistant requests are a separate work package."""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.planner.intent_router import preview_route
from app.schemas.assistant import RoutePreview, RoutePreviewRequest

router = APIRouter()


@router.post("/route-preview", response_model=RoutePreview)
async def route_preview(request: RoutePreviewRequest, user_id: CurrentUser) -> RoutePreview:
    # JWT authorizes inference only; no user ID or context is accepted from the model.
    return await preview_route(request)
