"""Replay synthetic inbox turns; opt-in provider evaluation, never Gmail or writes."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from app.assistant import inbox_chat
from app.assistant.summary import digest
from app.config import get_settings
from app.model_client.client import get_model_client
from app.schemas.inbox_chat import InboxChatRequest

CASES = [
    ("hey", "message", None),
    ("Show me all the GYG emails", "search", "GYG"),
    ("Find my flight emails", "search", "flight"),
    ("Show GYG emails from last month", "search", "GYG"),
    ("Find emails from alex@example.test", "search", ""),
    ("Show my latest emails", "search", ""),
    ("Summarise this thread", "continue", None),
    ("Find GYG emails and draft a reply", "continue", None),
    ("Write Alex an email thanking them", "continue", None),
]


def assets():
    return {**inbox_chat.assets(), "cases_hash": digest(CASES)}


async def evaluate():
    results = []

    class ReplayModel:
        async def generate(self, prompt, **kwargs):
            self.output = await get_model_client().generate(prompt, **kwargs)
            return self.output

    for instruction, kind, query in CASES:
        print("REPLAY", instruction, file=sys.stderr)
        model = ReplayModel()
        try:
            value = await inbox_chat.interpret(
                InboxChatRequest(instruction=instruction, timezone="Australia/Melbourne"),
                model=model,
                now=datetime(2026, 9, 23, 12, tzinfo=UTC),
            )
        except Exception:
            print("SYNTHETIC_REJECTED", getattr(model, "output", ("",))[0], file=sys.stderr)
            raise
        assert value["kind"] == kind, (instruction, value["kind"])
        if query is not None:
            assert value["filters"].query.casefold() == query.casefold(), instruction
        if instruction == "Find emails from alex@example.test":
            assert value["filters"].sender_email == "alex@example.test"
        if "last month" in instruction:
            assert value["filters"].received_from.isoformat() == "2026-07-31T14:00:00+00:00"
        results.append({"instruction": instruction, "kind": kind, "passed": True})
    return {**assets(), "cases": results, "mailbox_read": False, "external_actions": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(assets(), indent=2))
        return
    s = get_settings()
    if s.inference_provider != "bedrock" or s.email_writes_enabled or s.calendar_writes_enabled:
        raise SystemExit("Requires Bedrock with external writes disabled")
    result = asyncio.run(evaluate())
    result.update(
        provider="bedrock", model=(s.bedrock_small_model_id or s.bedrock_model_id).split("/")[-1]
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
