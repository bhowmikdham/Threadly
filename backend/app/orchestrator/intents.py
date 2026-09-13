"""Intent taxonomy — the contract between planner (4) and orchestrator (5).

Adding an intent = adding a dispatch arm in orchestrator.py + a planner rule
or schema entry + (usually) an endpoint. Keep this list short and boring.
"""
from enum import StrEnum


class Intent(StrEnum):
    SUMMARISE = "summarise"            # thread summary (cache -> pipeline)
    DRAFT = "draft"                    # reply drafting (RAG + 4b)
    FETCH_ENTITY = "fetch_entity"      # structured lookup — DB only, no model
    FETCH_COMMITMENTS = "fetch_commitments"  # cross-thread tracker — DB only
