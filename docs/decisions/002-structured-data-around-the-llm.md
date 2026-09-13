# 002 — Structured data goes around the LLM, never through it

Date: 2026-08-20 · Status: accepted

## Context

Entity lookups ("what's my flight number?"), commitment lists, and thread metadata
are already structured rows in postgres. Round-tripping them through a language
model adds latency, cost, and hallucination risk for zero gain — the exact
failure mode the proposal's literature review criticises in commercial tools.

## Decision

The orchestrator (module 5) dispatches by intent. FETCH_ENTITY-class intents are
answered straight from the entity store. Models are invoked only for what
genuinely needs generation (summaries, drafts) or for the extraction residue
tier-1 regex can't handle (module 6, tier 2).

## Consequences

- Extractor runs regex first and must cover 90%+ of entity volume; LLM handles the residue.
- Deterministic paths get golden tests (`backend/tests/goldens/`) — model paths get evals.
- The planner needs a reliable intent taxonomy (see `orchestrator/intents.py`).
