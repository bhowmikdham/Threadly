from fastapi import APIRouter

from .. import db

router = APIRouter()


@router.get("/healthz")
async def healthz():
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "service": "gateway"}
