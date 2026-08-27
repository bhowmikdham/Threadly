import datetime as dt
import json

from fastapi import APIRouter, Depends, Query
from threadly_common.cursor import decode_cursor, encode_cursor
from threadly_common.errors import APIError

from .. import config, db, security

router = APIRouter()


@router.get("/entities")
async def list_entities(
    user_id: int = Depends(security.current_user),
    type: str | None = Query(default=None),
    merchant: str | None = Query(default=None),
    window_days: int | None = Query(default=None, description="lookback window; omit for default, 0 = all time"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=None),
):
    """Structured lookups with progressive disclosure. First call returns the
    default window plus has_more; passing the cursor pages further back
    (the window no longer applies — the cursor is strictly keyset)."""
    limit = min(limit or config.ENTITY_PAGE_SIZE, 100)
    window = config.DEFAULT_ENTITY_WINDOW_DAYS if window_days is None else window_days

    base_conditions = ["user_id = $1"]
    args: list = [user_id]
    if type:
        args.append(type)
        base_conditions.append(f"type = ${len(args)}")
    if merchant:
        args.append(merchant)
        base_conditions.append(f"merchant ILIKE ${len(args)}")
    base_arg_count = len(args)

    conditions = list(base_conditions)
    window_start = None
    if cursor:
        try:
            before_ts, before_id = decode_cursor(cursor)
        except ValueError:
            raise APIError("invalid_cursor", "Cursor is malformed.", status=400)
        args.append(dt.datetime.fromisoformat(before_ts))
        ts_idx = len(args)
        args.append(before_id)
        conditions.append(f"(occurred_at, id) < (${ts_idx}, ${len(args)})")
    elif window:
        window_start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window)
        args.append(window_start)
        conditions.append(f"occurred_at >= ${len(args)}")

    args.append(limit + 1)  # fetch one extra row to learn has_more
    query = f"""
        SELECT id, type, key, value, merchant, source_msg_id, occurred_at
        FROM entities WHERE {' AND '.join(conditions)}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ${len(args)}
    """
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(query, *args)

        has_more = len(rows) > limit
        items = [dict(r) for r in rows[:limit]]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(last["occurred_at"].isoformat(), last["id"])

        # The window can hide older rows: if the page isn't full, check whether
        # anything exists beyond the window so has_more means "there IS more".
        if not has_more and window_start is not None:
            older_args = args[:base_arg_count] + [window_start]
            older = await conn.fetchval(
                f"SELECT 1 FROM entities WHERE {' AND '.join(base_conditions)} AND occurred_at < ${len(older_args)} LIMIT 1",
                *older_args,
            )
            if older:
                has_more = True
                if items:
                    last = items[-1]
                    next_cursor = encode_cursor(last["occurred_at"].isoformat(), last["id"])
                else:
                    next_cursor = encode_cursor(window_start.isoformat(), -1)

    for item in items:  # asyncpg returns jsonb as a string
        if isinstance(item.get("value"), str):
            item["value"] = json.loads(item["value"])
    return {
        "items": items,
        "has_more": has_more,
        "cursor": next_cursor,
        "window_days": None if cursor else window,
    }
