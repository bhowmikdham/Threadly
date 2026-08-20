"""Module 4 — PLANNER (build: W3).

Utterance (typed or voice-transcribed) -> structured Plan. Rules first; only
the residue goes to the small model (2b) with a constrained JSON schema —
never the 4b, and thinking stays OFF (latency + cost caps, module 8).
"""
from dataclasses import dataclass

from app.orchestrator.intents import Intent
from app.planner import rules


@dataclass
class Plan:
    intent: Intent
    slots: dict  # e.g. {"thread_id": ..., "entity_type": "flight"}
    source: str  # "rules" | "llm"


async def plan(utterance: str, context: dict | None = None) -> Plan:
    intent = rules.match(utterance)
    if intent is not None:
        return Plan(intent=intent, slots={}, source="rules")
    # TODO(W3): model_client.generate_json(model_small, plan_intent prompt from /ml/prompts)
    raise NotImplementedError("2b JSON fallback lands in W3")
