from fastapi import APIRouter, Depends, Query
from threadly_common.errors import APIError

from .. import db, security

router = APIRouter()


@router.get("/commitments")
async def list_commitments(
    user_id: int = Depends(security.current_user),
    status: str = Query(default="open"),
    direction: str | None = Query(default=None, description="inbound | outbound"),
    limit: int = Query(default=50, le=200),
):
    conditions = ["user_id = $1", "status = $2"]
    args: list = [user_id, status]
    if direction:
        args.append(direction)
        conditions.append(f"direction = ${len(args)}")
    args.append(limit)
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, description, direction, due_at, source_msg_id, status, created_at
            FROM commitments WHERE {' AND '.join(conditions)}
            ORDER BY due_at ASC NULLS LAST, id ASC
            LIMIT ${len(args)}
            """,
            *args,
        )
    return {"items": [dict(r) for r in rows]}


@router.post("/commitments/{commitment_id}/done")
async def mark_done(commitment_id: int, user_id: int = Depends(security.current_user)):
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE commitments SET status = 'done' WHERE id = $1 AND user_id = $2
            RETURNING id, description, direction, due_at, status
            """,
            commitment_id, user_id,
        )
    if not row:
        raise APIError("not_found", "Commitment not found.", status=404)
    return dict(row)
