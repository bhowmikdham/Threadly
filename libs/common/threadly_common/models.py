"""Shared request/response models — the vocabulary every service speaks."""
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    FETCH_ENTITY = "FETCH_ENTITY"  # structured lookup, goes around the LLM
    SUMMARISE = "SUMMARISE"        # thread summary, cache-first
    DRAFT = "DRAFT"                # email generation, RAG + LLM
    SEARCH = "SEARCH"              # free-text FTS over messages
    UNKNOWN = "UNKNOWN"            # planner couldn't decide; classifier fallback


class Plan(BaseModel):
    intent: Intent
    params: dict = Field(default_factory=dict)
    confidence: float = 1.0
    source: str = "rules"  # rules | classifier


class ChatRequest(BaseModel):
    message: str
    thread_id: int | None = None


class OrchestrateRequest(ChatRequest):
    user_id: int


class ClassifyRequest(BaseModel):
    text: str
    labels: list[str] | None = None


class GenerateRequest(BaseModel):
    prompt: str
    system: str | None = None
    max_tokens: int = 512
    small_model: bool = False  # route to the 2b (planner/JSON) tier
    stream: bool = True        # False = collect and return {"text", ...} JSON


class EmbedRequest(BaseModel):
    texts: list[str]
