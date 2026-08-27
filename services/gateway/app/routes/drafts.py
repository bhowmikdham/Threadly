from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from threadly_common.draft_state import TRANSITIONS, DraftStatus
from threadly_common.errors import APIError

from .. import clients, config, db, security

router = APIRouter()

DRAFT_COLS = "id, thread_id, intent, to_addrs, subject, body, status, model, gmail_msg_id, error, created_at, updated_at"


class DraftEdit(BaseModel):
    subject: str | None = None
    body: str | None = None
    to_addrs: list[str] | None = None


async def _get_draft(conn, draft_id: int, user_id: int) -> dict:
    row = await conn.fetchrow(f"SELECT {DRAFT_COLS} FROM drafts WHERE id = $1 AND user_id = $2", draft_id, user_id)
    if not row:
        raise APIError("not_found", "Draft not found.", status=404)
    return dict(row)


async def _transition(conn, draft_id: int, user_id: int, target: DraftStatus) -> dict:
    """Atomic guarded transition: the WHERE clause enforces the state machine
    so two concurrent requests can't both move the same draft."""
    allowed_from = [s.value for s, targets in TRANSITIONS.items() if target in targets]
    row = await conn.fetchrow(
        f"""
        UPDATE drafts SET status = $1, updated_at = now()
        WHERE id = $2 AND user_id = $3 AND status = ANY($4)
        RETURNING {DRAFT_COLS}
        """,
        target.value, draft_id, user_id, allowed_from,
    )
    if not row:
        current = await _get_draft(conn, draft_id, user_id)
        raise APIError(
            "illegal_transition",
            f"Draft is '{current['status']}'; cannot move to '{target.value}'.",
            status=409,
        )
    return dict(row)


@router.get("/drafts")
async def list_drafts(user_id: int = Depends(security.current_user), limit: int = 25, offset: int = 0):
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {DRAFT_COLS} FROM drafts WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            user_id, min(limit, 100), offset,
        )
    return {"items": [dict(r) for r in rows]}


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int, user_id: int = Depends(security.current_user)):
    async with db.pool().acquire() as conn:
        return await _get_draft(conn, draft_id, user_id)


@router.patch("/drafts/{draft_id}")
async def edit_draft(draft_id: int, body: DraftEdit, user_id: int = Depends(security.current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise APIError("empty_edit", "Nothing to update.", status=400)
    async with db.pool().acquire() as conn:
        draft = await _get_draft(conn, draft_id, user_id)
        if DraftStatus.EDITED not in TRANSITIONS[DraftStatus(draft["status"])]:
            raise APIError("illegal_transition", f"Draft is '{draft['status']}' and can no longer be edited.", status=409)
        sets = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(updates))
        args = list(updates.values())
        row = await conn.fetchrow(
            f"""
            UPDATE drafts SET {sets}, status = 'edited', updated_at = now()
            WHERE id = ${len(args) + 1} AND user_id = ${len(args) + 2}
            RETURNING {DRAFT_COLS}
            """,
            *args, draft_id, user_id,
        )
    return dict(row)


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, user_id: int = Depends(security.current_user)):
    async with db.pool().acquire() as conn:
        return await _transition(conn, draft_id, user_id, DraftStatus.APPROVED)


@router.post("/drafts/{draft_id}/discard")
async def discard_draft(draft_id: int, user_id: int = Depends(security.current_user)):
    async with db.pool().acquire() as conn:
        return await _transition(conn, draft_id, user_id, DraftStatus.DISCARDED)


@router.post("/drafts/{draft_id}/send")
async def send_draft(
    draft_id: int,
    user_id: int = Depends(security.current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """The only path from draft to outbound email. Requires prior approval;
    replays with the same Idempotency-Key return the original result."""
    async with db.pool().acquire() as conn:
        if idempotency_key:
            existing = await conn.fetchrow(
                f"SELECT {DRAFT_COLS} FROM drafts WHERE user_id = $1 AND idempotency_key = $2",
                user_id, idempotency_key,
            )
            if existing and existing["id"] != draft_id:
                raise APIError("idempotency_conflict", "Idempotency-Key already used for another draft.", status=409)
            if existing and existing["status"] == DraftStatus.SENT.value:
                return dict(existing)

        draft = await _transition(conn, draft_id, user_id, DraftStatus.SENDING)
        if idempotency_key:
            await conn.execute(
                "UPDATE drafts SET idempotency_key = $1 WHERE id = $2", idempotency_key, draft_id
            )

    # Actual delivery is owned by the gmail service (the only service that
    # talks to Google). Record the terminal state whatever happens.
    try:
        resp = await clients.http.post(
            f"{config.GMAIL_SERVICE_URL}/internal/send",
            json={"draft_id": draft_id, "user_id": user_id},
            headers=security.internal_headers(user_id),
        )
        payload = resp.json()
        ok = resp.status_code == 200
    except Exception as exc:
        ok, payload = False, {"error": {"message": str(exc)}}

    async with db.pool().acquire() as conn:
        if ok:
            row = await conn.fetchrow(
                f"""
                UPDATE drafts SET status = 'sent', gmail_msg_id = $1, error = NULL, updated_at = now()
                WHERE id = $2 AND user_id = $3 RETURNING {DRAFT_COLS}
                """,
                payload.get("gmail_msg_id"), draft_id, user_id,
            )
            return dict(row)
        error_msg = payload.get("error", {}).get("message", "send failed")
        await conn.execute(
            "UPDATE drafts SET status = 'failed', error = $1, updated_at = now() WHERE id = $2 AND user_id = $3",
            error_msg, draft_id, user_id,
        )
    raise APIError("send_failed", error_msg, status=502, details={"draft_id": draft_id})
