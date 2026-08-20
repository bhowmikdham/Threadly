"""Module 3 — SYNC WORKER (build: W1).

Pulls the user's mailbox through the Gmail API and upserts postgres.

Non-negotiables from the architecture doc:
- paginate ALL pages (nextPageToken loop — never trust a single page)
- incremental sync via users.gmail_history_id once the initial backfill is done
- clean before store: strip quoted replies, signatures, tracking cruft
  -> messages.body_clean (raw bodies are never persisted)
- upsert, never blind-insert: UNIQUE (user_id, gmail_msg_id) / (user_id, gmail_thread_id)
- classify at ingest (reply-or-not / priority) with the AI team's BERT
  classifier from /ml/classifier — stored so /threads?filter=needs_reply is a
  pure DB query (ADR 002)
"""


async def initial_backfill(user_id: int) -> None:
    """TODO(W1): full mailbox walk, batched, resumable."""
    raise NotImplementedError


async def incremental_sync(user_id: int) -> None:
    """TODO(W1): history.list from gmail_history_id; fall back to backfill on
    404 (expired sync cursor)."""
    raise NotImplementedError


def clean_body(raw_html_or_text: str) -> str:
    """TODO(W1): quote/signature stripping. Golden tests cover this (deterministic)."""
    raise NotImplementedError
