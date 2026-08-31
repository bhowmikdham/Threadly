import datetime as dt
import json

from fastapi import APIRouter, Depends, Query
from threadly_common.cursor import decode_cursor, encode_cursor
from threadly_common.errors import APIError
from threadly_common.merchant import FUZZY_MERCHANT_SQL, fuzzy_terms, merchant_candidates

from .. import config, db, security

router = APIRouter()


@router.get("/entities")
async def list_entities(
    user_id: int = Depends(security.current_user),
    type: str | None = Query(default=None),
    merchant: str | None = Query(default=None),
    key: str | None = Query(default=None, description="reference key filter, substring match"),
    window_days: int | None = Query(default=None, description="lookback window; omit for default, 0 = all time"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=None),
):
    """Structured lookups with progressive disclosure. First call returns the
    default window plus has_more; passing the cursor pages further back.
    Merchant matching is two-tier: substring/initialism first, then an
    edit-distance fallback for typos (GIG -> GYG) — response carries `fuzzy`."""
    limit = min(limit or config.ENTITY_PAGE_SIZE, 100)
    window = config.DEFAULT_ENTITY_WINDOW_DAYS if window_days is None else window_days

    cursor_ts = cursor_id = None
    if cursor:
        try:
            before_ts, cursor_id = decode_cursor(cursor)
            cursor_ts = dt.datetime.fromisoformat(before_ts)
        except ValueError:
            raise APIError("invalid_cursor", "Cursor is malformed.", status=400)

    window_start = None
    if not cursor and window:
        window_start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window)

    async def run_query(conn, fuzzy: bool):
        base_conditions = ["user_id = $1"]
        args: list = [user_id]
        if type:
            args.append(type)
            base_conditions.append(f"type = ${len(args)}")
        if key:
            args.append(f"%{key}%")
            base_conditions.append(f"key ILIKE ${len(args)}")
        if merchant:
            if fuzzy:
                args.append(fuzzy_terms(merchant))
                base_conditions.append(FUZZY_MERCHANT_SQL.format(param=f"${len(args)}"))
            else:
                # value->>'from' (the sender), never value::text — raw JSON
                # matching let the literal key name "from" pollute results (E1).
                args.append(merchant_candidates(merchant))
                base_conditions.append(f"(merchant ILIKE ANY(${len(args)}::text[]) OR value->>'from' ILIKE ANY(${len(args)}::text[]))")
        base_arg_count = len(args)

        conditions = list(base_conditions)
        if cursor_ts is not None:
            args.append(cursor_ts)
            ts_idx = len(args)
            args.append(cursor_id)
            conditions.append(f"(occurred_at, id) < (${ts_idx}, ${len(args)})")
        elif window_start is not None:
            args.append(window_start)
            conditions.append(f"occurred_at >= ${len(args)}")

        args.append(limit + 1)  # fetch one extra row to learn has_more
        rows = await conn.fetch(
            f"""
            SELECT id, type, key, value, merchant, source_msg_id, occurred_at
            FROM entities WHERE {' AND '.join(conditions)}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
        has_more = len(rows) > limit
        # The window can hide older rows: if the page isn't full, check whether
        # anything exists beyond the window so has_more means "there IS more".
        if not has_more and window_start is not None:
            older_args = args[:base_arg_count] + [window_start]
            has_more = bool(await conn.fetchval(
                f"SELECT 1 FROM entities WHERE {' AND '.join(base_conditions)} AND occurred_at < ${len(older_args)} LIMIT 1",
                *older_args,
            ))
        return rows, has_more

    fuzzy = False
    async with db.pool().acquire() as conn:
        rows, has_more = await run_query(conn, fuzzy=False)
        if not rows and not has_more and merchant and fuzzy_terms(merchant):
            rows, has_more = await run_query(conn, fuzzy=True)
            fuzzy = bool(rows) or has_more

    items = [dict(r) for r in rows[:limit]]
    for item in items:  # asyncpg returns jsonb as a string
        if isinstance(item.get("value"), str):
            item["value"] = json.loads(item["value"])

    next_cursor = None
    if has_more:
        if items:
            last = items[-1]
            next_cursor = encode_cursor(last["occurred_at"].isoformat(), last["id"])
        elif window_start is not None:
            next_cursor = encode_cursor(window_start.isoformat(), -1)

    return {
        "items": items,
        "has_more": has_more,
        "cursor": next_cursor,
        "window_days": None if cursor else window,
        "fuzzy": fuzzy,
    }
