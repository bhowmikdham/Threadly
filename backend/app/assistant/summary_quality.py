"""Concise summary release; historical summary/routing/Flow contracts stay intact."""

import json
import re

from pydantic import Field, model_validator

from app.api.errors import ApiError
from app.assistant import summary, summary_policy, summary_policy_v1
from app.model_client.structured import reject_duplicate_keys

RELEASE = "summary-quality-task-1.0.0"


def words(value: str) -> int:
    return len(value.split())


class ConciseSummary(summary.GeneratedSummary):
    overview: str = Field(min_length=1, max_length=1200, pattern=r"\S")
    decisions: list[summary.Claim] = Field(max_length=3)
    actions: list[summary.Claim] = Field(max_length=3)
    open_questions: list[str] = Field(max_length=2)

    @model_validator(mode="after")
    def concise_nonrepeating_fields(self):
        items = [c.text for c in [*self.decisions, *self.actions]] + self.open_questions
        if words(self.overview) > summary_policy.OVERVIEW_WORDS:
            raise ValueError("Summary overview exceeds word budget")
        if any(not s.strip() or words(s) > summary_policy.ITEM_WORDS for s in items):
            raise ValueError("Summary item exceeds word budget or is empty")
        if sum(words(s) for s in [self.overview, *items]) > summary_policy.TOTAL_WORDS:
            raise ValueError("Summary exceeds total word budget")
        canonical = [re.sub(r"\W+", " ", s.casefold()).strip() for s in [self.overview, *items]]
        if len(set(canonical)) != len(canonical):
            raise ValueError("Summary repeats a field")
        return self


def contract_hash(policy=None) -> str:
    policy = policy or summary_policy
    return summary.digest(
        {
            "version": policy.VERSION,
            "prompt": policy.PROMPT,
            "schema": ConciseSummary.model_json_schema(),
            "budgets": [
                policy.OVERVIEW_WORDS,
                policy.ITEM_WORDS,
                policy.TOTAL_WORDS,
            ],
            "validation": "bounded-nonrepeating-fields-native-source-validation-v1",
        }
    )


def wrap_release(base: dict) -> dict:
    return {"workflow": RELEASE, "base_release": base, "contract_hash": contract_hash()}


def policy_for_release(release: dict):
    if (
        set(release) == {"workflow", "base_release", "contract_hash"}
        and release["workflow"] == RELEASE
    ):
        for policy in (summary_policy, summary_policy_v1):
            if release["contract_hash"] == contract_hash(policy):
                return policy
    raise ApiError(503, "release_unavailable", "The saved summary release is unavailable.")


def unwrap_release(release: dict) -> dict:
    policy_for_release(release)
    return release["base_release"]


def make_prompt(snapshot: dict, instruction: str, *, policy=None) -> str:
    policy = policy or summary_policy
    # IDs, recipients and provider metadata remain outside model authority.
    messages = [
        {"number": i, "from": m["from_addr"], "sent_at": m["sent_at"], "body": m["body"]}
        for i, m in enumerate(snapshot["messages"], 1)
    ]
    return (
        policy.PROMPT
        + "\nUSER_SUMMARY_REQUEST_JSON:\n"
        + json.dumps(instruction)
        + "\nSOURCE_JSON:\n"
        + json.dumps(messages)
    )


def make_artifact(text: str, context_id: str, snapshot: dict) -> dict:
    if len(text) > 16000:
        raise ValueError("Summary output too large")
    ConciseSummary.model_validate(json.loads(text, object_pairs_hook=reject_duplicate_keys))
    # Stable frontend shape and backend-owned evidence/coverage; no model assumptions.
    return summary.make_artifact(text, context_id, snapshot)
