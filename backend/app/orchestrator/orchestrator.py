"""Module 5 — ORCHESTRATOR (build: W2+).

Single dispatch point for user requests. The rule that shapes everything here
(ADR 002): structured data goes AROUND the LLM, never through it.

Dispatch table (from the architecture doc):
- FETCH_ENTITY / FETCH_COMMITMENTS -> entity store (postgres). No model call.
- SUMMARISE -> summaries cache hit on (thread_id, last_msg_id)? return it.
               miss -> clean thread -> prompt (from /ml/prompts) -> model_client -> cache.
- DRAFT     -> RAG retrieve (7, user's sent-mail voice examples) -> 4b via
               model_client -> stream tokens -> persist drafts row.
"""
from app.orchestrator.intents import Intent
from app.planner.planner import Plan


async def dispatch(user_id: int, p: Plan):
    match p.intent:
        case Intent.FETCH_ENTITY | Intent.FETCH_COMMITMENTS:
            raise NotImplementedError("W2: entity store query")
        case Intent.SUMMARISE:
            raise NotImplementedError("W1/W2: cache -> summary pipeline")
        case Intent.DRAFT:
            raise NotImplementedError("W3: RAG + 4b")
