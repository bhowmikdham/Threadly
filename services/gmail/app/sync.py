"""Sync worker: pull messages, clean, upsert postgres, extract entities, and
hand sent mail to the orchestrator for RAG indexing. Idempotent end to end —
every write is an upsert, so re-syncing is always safe."""
import asyncio
import json
import logging

import httpx

from . import config, db, extractor, gmail_client

log = logging.getLogger(__name__)

http = httpx.AsyncClient(timeout=60.0)


async def sync_user(user_id: int) -> dict:
    async with db.pool().acquire() as conn:
        has_tokens = bool(await conn.fetchval(
            "SELECT 1 FROM oauth_tokens WHERE user_id = $1 AND provider = 'google' AND enc_refresh_token IS NOT NULL",
            user_id,
        ))
        last_seen = await conn.fetchval("SELECT max(sent_at) FROM messages WHERE user_id = $1", user_id)

    client = gmail_client.client_for(user_id, has_tokens)
    messages = await client.list_messages(after=last_seen)

    new_msgs, new_entities, rag_docs = 0, 0, []
    async with db.pool().acquire() as conn:
        for msg in messages:
            thread_id = await conn.fetchval(
                """
                INSERT INTO threads (user_id, gmail_thread_id, subject, last_msg_at, msg_count)
                VALUES ($1, $2, $3, $4, 1)
                ON CONFLICT (user_id, gmail_thread_id) DO UPDATE SET
                    subject = COALESCE(threads.subject, EXCLUDED.subject),
                    last_msg_at = GREATEST(threads.last_msg_at, EXCLUDED.last_msg_at)
                RETURNING id
                """,
                user_id, msg["gmail_thread_id"], msg.get("subject"), msg["sent_at"],
            )
            inserted = await conn.fetchval(
                """
                INSERT INTO messages (user_id, thread_id, gmail_msg_id, from_addr, to_addrs, is_sent, sent_at, subject, snippet, body_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (user_id, gmail_msg_id) DO NOTHING
                RETURNING id
                """,
                user_id, thread_id, msg["gmail_msg_id"], msg.get("from_addr"), msg.get("to_addrs", []),
                msg.get("is_sent", False), msg["sent_at"], msg.get("subject"), msg.get("snippet"), msg.get("body_text"),
            )
            if not inserted:
                continue
            new_msgs += 1
            await conn.execute(
                "UPDATE threads SET msg_count = (SELECT count(*) FROM messages WHERE thread_id = $1) WHERE id = $1",
                thread_id,
            )
            if msg.get("is_sent"):
                rag_docs.append({"id": msg["gmail_msg_id"], "text": msg.get("body_text") or ""})
            else:
                for entity in extractor.extract(msg):
                    done = await conn.execute(
                        """
                        INSERT INTO entities (user_id, type, key, value, merchant, source_msg_id, occurred_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (user_id, type, key) DO NOTHING
                        """,
                        user_id, entity["type"], entity["key"], json.dumps(entity["value"]),
                        entity["merchant"], entity["source_msg_id"], entity["occurred_at"],
                    )
                    if done.endswith("1"):
                        new_entities += 1

    if rag_docs:
        try:
            await http.post(
                f"{config.ORCHESTRATOR_URL}/internal/rag/index",
                json={"user_id": user_id, "docs": rag_docs},
                headers={"X-Threadly-Internal": config.INTERNAL_TOKEN},
            )
        except Exception:
            log.exception("RAG indexing failed for user %s (will retry next sync)", user_id)

    result = {"user_id": user_id, "messages": new_msgs, "entities": new_entities, "rag_docs": len(rag_docs)}
    log.info("sync done: %s", result)
    return result


async def sync_all() -> list[dict]:
    async with db.pool().acquire() as conn:
        if config.DEV_MODE:
            user_ids = [r["id"] for r in await conn.fetch("SELECT id FROM users")]
        else:
            user_ids = [r["user_id"] for r in await conn.fetch(
                "SELECT user_id FROM oauth_tokens WHERE provider = 'google' AND enc_refresh_token IS NOT NULL"
            )]
    results = []
    for user_id in user_ids:
        try:
            results.append(await sync_user(user_id))
        except Exception:
            log.exception("sync failed for user %s", user_id)
    return results


async def sync_loop() -> None:
    while True:
        try:
            await sync_all()
        except Exception:
            log.exception("sync sweep crashed; continuing")
        await asyncio.sleep(config.SYNC_INTERVAL_SECONDS)
