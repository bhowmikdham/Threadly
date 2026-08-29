"""Sync worker: pull messages, clean, upsert postgres, extract entities, and
hand sent mail to the orchestrator for RAG indexing. Idempotent end to end —
every write is an upsert, so re-syncing is always safe."""
import asyncio
import json
import logging

import httpx

from . import config, db, extractor, gmail_client, normalize

log = logging.getLogger(__name__)

http = httpx.AsyncClient(timeout=60.0)


async def _upsert_entities(conn, user_id: int, entities: list[dict]) -> int:
    inserted = 0
    for entity in entities:
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
            inserted += 1
    return inserted


async def _tier2_extract(user_id: int, candidates: list[dict]) -> int:
    """LLM extraction for the residue tier-1 missed. Best-effort: any failure
    just leaves the message for a future improvement, never blocks sync."""
    extracted = 0
    for msg in candidates:
        try:
            resp = await http.post(
                f"{config.INFERENCE_URL}/v1/generate",
                json={"prompt": extractor.tier2_prompt(msg), "max_tokens": 200, "small_model": True, "stream": False},
                headers={"X-Threadly-Internal": config.INTERNAL_TOKEN},
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("backend") == "stub":
                continue  # dev stub output is not real extraction
            entities = extractor.parse_tier2_response(payload.get("text", ""), msg)
            if entities:
                async with db.pool().acquire() as conn:
                    extracted += await _upsert_entities(conn, user_id, entities)
        except Exception:
            log.exception("tier-2 extraction failed for message %s", msg.get("gmail_msg_id"))
    return extracted


LABEL_TASKS = ("priority", "action", "category")


async def _label_messages(batch: list[tuple[int, str]]) -> int:
    """Run the AI team's per-email classifiers (or their fallbacks) on newly
    synced inbound mail and store the labels. Best-effort per message."""
    labeled = 0
    for msg_db_id, text in batch:
        try:
            labels = {}
            for task in LABEL_TASKS:
                resp = await http.post(
                    f"{config.INFERENCE_URL}/v1/classify",
                    json={"text": text[:2000], "task": task},
                    headers={"X-Threadly-Internal": config.INTERNAL_TOKEN},
                )
                resp.raise_for_status()
                labels[task] = resp.json()
            async with db.pool().acquire() as conn:
                await conn.execute(
                    "UPDATE messages SET priority = $1, action = $2, category = $3, labels = $4 WHERE id = $5",
                    labels["priority"]["label"], labels["action"]["label"], labels["category"]["label"],
                    json.dumps(labels), msg_db_id,
                )
            labeled += 1
        except Exception:
            log.exception("labeling failed for message %s (will stay unlabeled)", msg_db_id)
    return labeled


async def sync_user(user_id: int) -> dict:
    async with db.pool().acquire() as conn:
        has_tokens = bool(await conn.fetchval(
            "SELECT 1 FROM oauth_tokens WHERE user_id = $1 AND provider = 'google' AND enc_refresh_token IS NOT NULL",
            user_id,
        ))
        last_seen = await conn.fetchval("SELECT max(sent_at) FROM messages WHERE user_id = $1", user_id)

    client = gmail_client.client_for(user_id, has_tokens)
    messages = await client.list_messages(after=last_seen)

    new_msgs, new_entities, rag_docs, tier2_candidates, label_batch = 0, 0, [], [], []
    async with db.pool().acquire() as conn:
        for msg in messages:
            # One normalization pass; extraction, FTS, labeling, RAG and
            # summaries all consume the cleaned text from here on.
            msg["body_text"] = normalize.clean_email_text(msg.get("body_text") or "")
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
            # Commitments come from both directions: inbound asks, outbound promises.
            for commitment in extractor.extract_commitments(msg):
                await conn.execute(
                    """
                    INSERT INTO commitments (user_id, description, direction, due_at, source_msg_id)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, source_msg_id, description) DO NOTHING
                    """,
                    user_id, commitment["description"], commitment["direction"],
                    commitment["due_at"], commitment["source_msg_id"],
                )
            if msg.get("is_sent"):
                rag_docs.append({"id": msg["gmail_msg_id"], "text": msg.get("body_text") or ""})
            else:
                tier1 = extractor.extract(msg)
                new_entities += await _upsert_entities(conn, user_id, tier1)
                if config.TIER2_EXTRACTION and extractor.needs_tier2(msg, tier1):
                    tier2_candidates.append(msg)
                if config.MESSAGE_LABELING:
                    label_batch.append((inserted, f"{msg.get('subject', '')}\n{msg.get('body_text') or msg.get('snippet') or ''}"))

    if tier2_candidates:
        new_entities += await _tier2_extract(user_id, tier2_candidates)

    labeled = await _label_messages(label_batch) if label_batch else 0

    if rag_docs:
        try:
            await http.post(
                f"{config.ORCHESTRATOR_URL}/internal/rag/index",
                json={"user_id": user_id, "docs": rag_docs},
                headers={"X-Threadly-Internal": config.INTERNAL_TOKEN},
            )
        except Exception:
            log.exception("RAG indexing failed for user %s (will retry next sync)", user_id)

    result = {"user_id": user_id, "messages": new_msgs, "entities": new_entities, "labeled": labeled, "rag_docs": len(rag_docs)}
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
