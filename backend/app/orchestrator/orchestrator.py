"""Module 5 — ORCHESTRATOR (W1: SUMMARISE pipeline implemented).

The rule that shapes everything here (ADR 002): structured data goes AROUND
the LLM, never through it.

SUMMARISE:
    cache hit on (thread_pk, last_msg_id)  -> yield cached text, done (no model)
    miss -> build thread text from postgres -> prompt -> model_client stream
         -> persist to summaries -> done
Emits SummaryEvent items the SSE route maps 1:1 onto the wire contract.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db import repositories as repo
from app.db.models import Thread
from app.model_client.client import get_model_client
from app.orchestrator.prompts import get_prompt, prompts_version

_THREAD_CHAR_BUDGET = 12_000  # keep 4b prompts inside local context; W2: openrouter for longer


@dataclass
class SummaryEvent:
    kind: str  # "token" | "done" | "cached"
    text: str = ""
    provider: str | None = None


def _render_thread(messages, char_budget: int = _THREAD_CHAR_BUDGET) -> str:
    """Newest-last transcript, trimmed oldest-first to fit the budget."""
    blocks: list[str] = []
    for m in messages:
        who = "ME" if m.is_from_user else (m.from_addr or "unknown")
        when = m.sent_at.isoformat() if m.sent_at else "?"
        blocks.append(f"[{when}] {who}:\n{m.body_clean or ''}")
    text = "\n\n---\n\n".join(blocks)
    return text[-char_budget:] if len(text) > char_budget else text


async def summarise_thread(
    session: AsyncSession, user_id: int, gmail_thread_id: str
) -> AsyncIterator[SummaryEvent]:
    thread = (
        await session.execute(
            select(Thread)
            .where(
                Thread.user_id == user_id,
                Thread.gmail_thread_id == gmail_thread_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if thread is None:
        raise ApiError(404, "not_found", "Unknown thread — run /sync first?")
    if not thread.last_msg_id:
        raise ApiError(409, "thread_empty", "Thread has no synced messages yet.")

    cached = await repo.get_cached_summary(session, thread.id, thread.last_msg_id)
    if cached is not None:
        body = cached.body
        await session.commit()
        yield SummaryEvent("cached", body, provider="cache")
        yield SummaryEvent("done", provider="cache")
        return

    messages = await repo.thread_messages(session, thread.id)
    if not messages:
        raise ApiError(409, "thread_empty", "Thread has no synced messages yet.")

    prompt = get_prompt("summarise_thread", thread_text=_render_thread(messages))
    thread_pk, last_msg_id, version = thread.id, thread.last_msg_id, thread.version
    await session.commit()  # Release the snapshot lock before model I/O.
    stream, info = await get_model_client().stream(prompt, max_tokens=500)

    parts: list[str] = []
    async for token in stream:
        parts.append(token)
        yield SummaryEvent("token", token, provider=info.provider)

    body = "".join(parts).strip()
    stored = await repo.store_summary(
        session,
        user_id=user_id,
        thread_pk=thread_pk,
        last_msg_id=last_msg_id,
        expected_version=version,
        body=body,
        model_used=info.storage_label(prompts_version()),
    )
    await session.commit()
    if not stored:
        raise ApiError(409, "context_changed", "Thread changed during summary; request it again.")
    yield SummaryEvent("done", provider=info.provider)
