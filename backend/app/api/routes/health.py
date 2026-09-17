"""Liveness + readiness. /healthz never touches dependencies; /readyz does."""

from fastapi import APIRouter

from app.api.errors import ApiError
from app.config import get_settings

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "version": get_settings().app_version}


@router.get("/readyz")
async def readyz() -> dict:
    checks = {"postgres": False, "chroma": False, "workflow_configuration": False}
    try:
        from app.workflows.registry import load_manifest

        load_manifest()
        from app.workflows.auxiliary import load_manifest as auxiliary_manifest

        auxiliary_manifest()
        checks["workflow_configuration"] = True
    except ApiError:
        pass
    try:
        from sqlalchemy import text

        from app.db.engine import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        pass
    try:
        import httpx

        settings = get_settings()
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{settings.chroma_url}/api/v2/heartbeat")
            checks["chroma"] = r.status_code == 200
    except Exception:
        pass
    if not all(checks.values()):
        raise ApiError(503, "not_ready", "A dependency is down.", detail=checks)
    return {"status": "ok", **checks}
