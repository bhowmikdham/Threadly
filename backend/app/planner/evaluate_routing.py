"""Opt-in synthetic router/draft replay. Never reads mail or executes actions."""

import argparse
import asyncio
import json
from types import SimpleNamespace

from app.assistant import drafting, routing
from app.assistant.summary import digest
from app.config import get_settings
from app.model_client.client import get_model_client

REPLAY_VERSION = "compose-routing-replay-1.0"
COMPOSE = (
    "Write a short email to Alex thanking them for the project update. "
    "Say I will review it tomorrow. Do not add a signature."
)
CASES = [
    ("compose", COMPOSE, False, False, ["draft_new"]),
    (
        "summarise",
        "Give me a concise summary of this email, without recommendations.",
        True,
        False,
        ["summarise_thread"],
    ),
    (
        "reply",
        "Draft a polite reply thanking the sender, but do not send it.",
        True,
        True,
        ["draft_reply"],
    ),
    (
        "plan_schedule",
        "Check if tomorrow at 4 PM is free; do not book anything.",
        False,
        False,
        ["check_time"],
    ),
    (
        "summarise",
        "Summarise this thread and draft a reply; do not send anything.",
        True,
        True,
        ["summarise_thread", "draft_reply"],
    ),
]


async def run(model):
    results = []
    for index, (hint, instruction, source, reply, operations) in enumerate(CASES):
        for repeat in range(3 if index == 0 else 1):
            route = await routing.route_request(
                instruction,
                hint,
                "synthetic-context" if source else None,
                {"messages": []} if source else None,
                model,
                draft_input={
                    "to": ["qa@example.test"],
                    "reply": {"selected": True} if reply else None,
                },
            )
            decision = route["decision"]
            assert decision["intent"] in ({"summarise", "reply"} if index == 4 else {hint}), (
                index,
                repeat,
                decision["intent"],
                decision["operations"],
            )
            assert decision["operations"] == operations
            assert decision["parameters"]["recipient_refs"] == []
            assert decision["requested_action"] == "none"
            if index == 0:
                assert decision["status"] == "ready"
            results.append({"case": index, "repeat": repeat, "passed": True})
    envelope = {
        "to": ["qa@example.test"],
        "cc": [],
        "bcc": [],
        "reply": None,
        "reply_message_id": None,
        "from_address": "tester@example.test",
    }
    text, info = await model.generate(
        drafting.make_prompt(COMPOSE, None, envelope, "new"), max_tokens=2500
    )
    claim = SimpleNamespace(
        snapshot=None,
        context_id=None,
        draft_input=envelope,
        task_id="synthetic-test",
        instruction=COMPOSE,
    )
    artifact = drafting.make_artifact(text, claim, "new")
    body = artifact["content"]["body"].casefold()
    assert all(word in body for word in ("thank", "update", "review", "tomorrow"))
    assert not any(term in body for term in ("feedback", "get back", "best regards", "sincerely"))
    assert not artifact["content"]["unresolved_fields"]
    return {
        "replay": REPLAY_VERSION,
        "cases_hash": digest(CASES),
        "routing_release": routing.release_manifest(),
        "provider": info.provider,
        "model": info.model,
        "routes": results,
        "draft_passed": True,
        "external_actions": False,
        "mailbox_read": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Invoke configured Bedrock; billed")
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "replay": REPLAY_VERSION,
                    "cases": CASES,
                    "cases_hash": digest(CASES),
                    "live_verified": False,
                },
                indent=2,
            )
        )
        return
    settings = get_settings()
    if settings.inference_provider != "bedrock":
        raise SystemExit("Live replay requires configured Bedrock")
    if settings.email_writes_enabled or settings.calendar_writes_enabled:
        raise SystemExit("Keep provider writes disabled for this staging replay")
    print(json.dumps(asyncio.run(run(get_model_client())), indent=2))


if __name__ == "__main__":
    main()
