"""The dispatch engine: one chat request in, one SSE stream out.
Structured data (entities, search hits) goes AROUND the LLM; only prose
(summaries, drafts, answers) goes through it."""
import datetime as dt
import json
import logging
import uuid
from collections.abc import AsyncIterator

from threadly_common.cursor import encode_cursor
from threadly_common.merchant import FUZZY_MERCHANT_SQL, fuzzy_terms, merchant_candidates
from threadly_common.models import Intent, Plan
from threadly_common.sse import (
    EVENT_DONE, EVENT_DRAFT, EVENT_ENTITIES, EVENT_ERROR, EVENT_META,
    EVENT_RESULTS, EVENT_SUMMARY, EVENT_TOKEN, sse_event,
)

from . import config, db, inference_client, planner, prompts, rag

log = logging.getLogger(__name__)


async def handle(user_id: int, message: str, thread_id: int | None, request_id: str | None = None) -> AsyncIterator[str]:
    request_id = request_id or str(uuid.uuid4())
    the_plan = planner.plan(message, has_thread=thread_id is not None)

    if the_plan.intent == Intent.UNKNOWN:
        # Classifier fallback: BERT (or keyword rules) in the inference service.
        try:
            verdict = await inference_client.classify(message)
            the_plan = Plan(
                intent=Intent(verdict["label"]),
                params={"query": message, "instruction": message},
                confidence=verdict.get("confidence", 0.5),
                source=verdict.get("source", "classifier"),
            )
        except Exception:
            log.exception("classifier fallback failed")
            the_plan = Plan(intent=Intent.SEARCH, params={"query": message}, confidence=0.2, source="fallback")

    yield sse_event(EVENT_META, {
        "request_id": request_id,
        "intent": the_plan.intent.value,
        "params": the_plan.params,
        "planner_source": the_plan.source,
    })

    try:
        if the_plan.intent == Intent.FETCH_ENTITY:
            async for chunk in _fetch_entities(user_id, the_plan, message):
                yield chunk
        elif the_plan.intent == Intent.SUMMARISE:
            async for chunk in _summarise(user_id, thread_id):
                yield chunk
        elif the_plan.intent == Intent.DRAFT:
            async for chunk in _draft(user_id, thread_id, the_plan.params.get("instruction", message)):
                yield chunk
        else:  # SEARCH and anything unclassifiable
            async for chunk in _search(user_id, the_plan.params.get("query", message)):
                yield chunk
        yield sse_event(EVENT_DONE, {"request_id": request_id})
    except Exception:
        log.exception("orchestration failed (request %s)", request_id)
        yield sse_event(EVENT_ERROR, {"error": {
            "code": "orchestration_failed",
            "message": "Something went wrong handling this request.",
            "request_id": request_id,
        }})


async def _fetch_entities(user_id: int, the_plan: Plan, message: str) -> AsyncIterator[str]:
    """Pure SQL — no LLM tokens spent. Progressive disclosure via cursor."""
    params = the_plan.params
    if params.get("type") == "commitment":
        async for chunk in _fetch_commitments(user_id):
            yield chunk
        return
    window_days = params.get("window_days") or config.DEFAULT_ENTITY_WINDOW_DAYS
    limit = config.ENTITY_PAGE_SIZE
    window_start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)

    async def run_query(conn, fuzzy: bool):
        base_conditions = ["user_id = $1"]
        args: list = [user_id]
        if params.get("type"):
            args.append(params["type"])
            base_conditions.append(f"type = ${len(args)}")
        if params.get("key"):
            # Pasted reference ("order GYG-84640") narrows to that key (E4).
            args.append(f"%{params['key']}%")
            base_conditions.append(f"key ILIKE ${len(args)}")
        if params.get("merchant"):
            if fuzzy:
                # Edit-distance fallback: GIG finds GYG, GYGGG squeezes to GYG.
                args.append(fuzzy_terms(params["merchant"]))
                base_conditions.append(FUZZY_MERCHANT_SQL.format(param=f"${len(args)}"))
            else:
                # Substring/initialism match against the derived merchant OR the
                # sender value — value->>'from', never value::text, so the JSON
                # key names can't produce false matches (E1).
                args.append(merchant_candidates(params["merchant"]))
                base_conditions.append(f"(merchant ILIKE ANY(${len(args)}::text[]) OR value->>'from' ILIKE ANY(${len(args)}::text[]))")
        base_args = list(args)

        args.append(window_start)
        conditions = base_conditions + [f"occurred_at >= ${len(args)}"]
        args.append(limit + 1)
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
        # If the window page isn't full, has_more must still say whether older
        # rows exist beyond the window — that's the "want more?" signal.
        if not has_more:
            older_args = base_args + [window_start]
            has_more = bool(await conn.fetchval(
                f"SELECT 1 FROM entities WHERE {' AND '.join(base_conditions)} AND occurred_at < ${len(older_args)} LIMIT 1",
                *older_args,
            ))
        return rows, has_more

    fuzzy = False
    async with db.pool().acquire() as conn:
        rows, has_more = await run_query(conn, fuzzy=False)
        if not rows and not has_more and params.get("merchant") and fuzzy_terms(params["merchant"]):
            rows, has_more = await run_query(conn, fuzzy=True)
            fuzzy = bool(rows) or has_more

    items = [dict(r) for r in rows[:limit]]
    for item in items:  # asyncpg returns jsonb as a string
        if isinstance(item.get("value"), str):
            item["value"] = json.loads(item["value"])
    cursor = None
    if has_more:
        cursor = (
            encode_cursor(items[-1]["occurred_at"].isoformat(), items[-1]["id"])
            if items else encode_cursor(window_start.isoformat(), -1)
        )

    yield sse_event(EVENT_ENTITIES, {
        "items": items,
        "has_more": has_more,
        "cursor": cursor,
        "window_days": window_days,
        "fuzzy": fuzzy,
    })

    # Human-readable line the frontend can drop straight into the chat.
    label = params.get("type", "result")
    merchant = f" {params['merchant']}" if params.get("merchant") else ""
    if not items:
        text = f"I couldn't find any{merchant} {label}s in the last {window_days} days."
        if has_more:
            text += " There are older ones though — want me to look further back?"
    elif fuzzy:
        matched = sorted({i["merchant"] for i in items if i.get("merchant")})
        closest = " / ".join(matched) if matched else "a close match"
        text = (
            f"Nothing under '{params['merchant']}', but {closest} looks like what you meant — "
            f"found {len(items)} {label}{'s' if len(items) != 1 else ''} from the last {window_days} days."
        )
        if has_more:
            text += " There are older ones too — want me to keep looking further back?"
    else:
        text = f"Found {len(items)}{merchant} {label}{'s' if len(items) != 1 else ''} from the last {window_days} days."
        if has_more:
            text += " There are older ones too — want me to keep looking further back?"
    yield sse_event(EVENT_TOKEN, {"text": text})


async def _fetch_commitments(user_id: int) -> AsyncIterator[str]:
    """Open commitments, soonest deadline first. Small result set — no cursor."""
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, description, direction, due_at, source_msg_id, status
            FROM commitments WHERE user_id = $1 AND status = 'open'
            ORDER BY due_at ASC NULLS LAST, id ASC
            LIMIT 50
            """,
            user_id,
        )
    items = [dict(r) for r in rows]
    yield sse_event(EVENT_ENTITIES, {"items": items, "has_more": False, "cursor": None, "entity_type": "commitment"})

    if not items:
        yield sse_event(EVENT_TOKEN, {"text": "You're all clear — no open commitments found in your mail."})
        return
    promises = sum(1 for i in items if i["direction"] == "outbound")
    asks = len(items) - promises
    parts = []
    if promises:
        parts.append(f"{promises} thing{'s' if promises != 1 else ''} you promised")
    if asks:
        parts.append(f"{asks} thing{'s' if asks != 1 else ''} people asked of you")
    yield sse_event(EVENT_TOKEN, {"text": f"You have {' and '.join(parts)}, sorted by deadline."})


async def _summarise(user_id: int, thread_id: int | None) -> AsyncIterator[str]:
    async with db.pool().acquire() as conn:
        if thread_id is None:
            thread_id = await conn.fetchval(
                "SELECT id FROM threads WHERE user_id = $1 ORDER BY last_msg_at DESC NULLS LAST LIMIT 1", user_id
            )
        if thread_id is None:
            yield sse_event(EVENT_TOKEN, {"text": "There's nothing in your inbox to summarise yet."})
            return

        messages = await conn.fetch(
            """
            SELECT gmail_msg_id, from_addr, sent_at, snippet, body_text
            FROM messages WHERE thread_id = $1 AND user_id = $2 ORDER BY sent_at ASC
            """,
            thread_id, user_id,
        )
        if not messages:
            yield sse_event(EVENT_TOKEN, {"text": "That thread has no messages."})
            return

        # Cache key (thread_id, last_msg_id): any new message invalidates it.
        last_msg_id = messages[-1]["gmail_msg_id"]
        cached = await conn.fetchrow(
            "SELECT text, model FROM summaries WHERE thread_id = $1 AND last_msg_id = $2", thread_id, last_msg_id
        )

    if cached:
        yield sse_event(EVENT_SUMMARY, {"thread_id": thread_id, "text": cached["text"], "model": cached["model"], "cached": True})
        return

    thread_text = "\n\n".join(
        f"From: {m['from_addr']} at {m['sent_at']}\n{(m['body_text'] or m['snippet'] or '')}" for m in messages
    )[: config.THREAD_CONTEXT_CHAR_CAP]

    text_parts: list[str] = []
    model = None
    async for evt in inference_client.generate_stream(
        prompts.render("summarise_thread", thread_text=thread_text), system=prompts.system_prompt()
    ):
        if evt["event"] == EVENT_TOKEN:
            text_parts.append(evt["data"]["text"])
            yield sse_event(EVENT_TOKEN, evt["data"])
        elif evt["event"] == EVENT_DONE:
            model = evt["data"].get("model")

    summary_text = "".join(text_parts).strip()
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO summaries (user_id, thread_id, last_msg_id, model, text) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (thread_id, last_msg_id) DO UPDATE SET text = EXCLUDED.text, model = EXCLUDED.model
            """,
            user_id, thread_id, last_msg_id, model, summary_text,
        )
    yield sse_event(EVENT_SUMMARY, {"thread_id": thread_id, "text": summary_text, "model": model, "cached": False})


async def _draft(user_id: int, thread_id: int | None, instruction: str) -> AsyncIterator[str]:
    thread_context, subject, to_addrs = "", None, []
    async with db.pool().acquire() as conn:
        if thread_id is not None:
            thread = await conn.fetchrow(
                "SELECT subject FROM threads WHERE id = $1 AND user_id = $2", thread_id, user_id
            )
            if thread:
                subject = f"Re: {thread['subject']}" if thread["subject"] else None
                messages = await conn.fetch(
                    """
                    SELECT from_addr, is_sent, sent_at, body_text, snippet FROM messages
                    WHERE thread_id = $1 AND user_id = $2 ORDER BY sent_at ASC
                    """,
                    thread_id, user_id,
                )
                thread_context = "\n\n".join(
                    f"From: {m['from_addr']}\n{(m['body_text'] or m['snippet'] or '')}" for m in messages
                )[-config.THREAD_CONTEXT_CHAR_CAP:]
                last_inbound = next((m for m in reversed(messages) if not m["is_sent"]), None)
                if last_inbound and last_inbound["from_addr"]:
                    to_addrs = [last_inbound["from_addr"]]

    rag_examples = await rag.retrieve(user_id, instruction)

    body_parts: list[str] = []
    model = None
    async for evt in inference_client.generate_stream(
        prompts.render(
            "draft_email",
            instruction=instruction,
            thread_context=thread_context or "(new email, no thread)",
            rag_examples="\n---\n".join(rag_examples) or "(none available)",
        ),
        system=prompts.system_prompt(),
    ):
        if evt["event"] == EVENT_TOKEN:
            body_parts.append(evt["data"]["text"])
            yield sse_event(EVENT_TOKEN, evt["data"])
        elif evt["event"] == EVENT_DONE:
            model = evt["data"].get("model")

    body = "".join(body_parts).strip()
    if subject is None:
        subject = " ".join(instruction.split()[:8])

    async with db.pool().acquire() as conn:
        draft_id = await conn.fetchval(
            """
            INSERT INTO drafts (user_id, thread_id, intent, to_addrs, subject, body, status, model)
            VALUES ($1, $2, 'DRAFT', $3, $4, $5, 'generated', $6) RETURNING id
            """,
            user_id, thread_id, to_addrs, subject, body, model,
        )
    yield sse_event(EVENT_DRAFT, {
        "draft_id": draft_id,
        "status": "generated",
        "to_addrs": to_addrs,
        "subject": subject,
        "body": body,
        "model": model,
    })


async def _search(user_id: int, query: str) -> AsyncIterator[str]:
    async with db.pool().acquire() as conn:
        # Stopword-only questions ("what is it about?") produce an empty
        # tsquery: guide the user instead of pretending to search (E9).
        meaningful = await conn.fetchval("SELECT numnode(plainto_tsquery('english', $1))", query)
        if not meaningful:
            yield sse_event(EVENT_RESULTS, {"items": [], "count": 0, "reason": "query_too_general"})
            yield sse_event(EVENT_TOKEN, {
                "text": "That's a bit too general for me to search on — try naming a person, company, or topic."
            })
            return
        rows = await conn.fetch(
            """
            SELECT m.id, m.thread_id, m.from_addr, m.sent_at, t.subject,
                   ts_headline('english', coalesce(m.body_text, m.snippet, ''),
                               plainto_tsquery('english', $2),
                               'MaxWords=30, MinWords=10') AS snippet
            FROM messages m JOIN threads t ON t.id = m.thread_id
            WHERE m.user_id = $1 AND m.tsv @@ plainto_tsquery('english', $2)
            ORDER BY ts_rank(m.tsv, plainto_tsquery('english', $2)) DESC
            LIMIT 5
            """,
            user_id, query,
        )

    hits = [dict(r) for r in rows]
    yield sse_event(EVENT_RESULTS, {"items": hits, "count": len(hits)})

    if not hits:
        yield sse_event(EVENT_TOKEN, {"text": "I couldn't find anything matching that in your mail."})
        return

    snippets = "\n\n".join(f"[{h['subject']}] from {h['from_addr']}: {h['snippet']}" for h in hits)
    async for evt in inference_client.generate_stream(
        prompts.render("search_answer", query=query, snippets=snippets),
        system=prompts.system_prompt(),
        max_tokens=256,
    ):
        if evt["event"] == EVENT_TOKEN:
            yield sse_event(EVENT_TOKEN, evt["data"])
