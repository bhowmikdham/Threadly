"""Synthetic selected-thread regression: routing, summary, reply and quoted answers."""

import argparse
import asyncio
import json
from types import SimpleNamespace

from app.assistant import drafting, grounded_answer, routing, summary_quality
from app.assistant.summary import digest
from app.config import get_settings
from app.model_client.client import get_model_client

VERSION = "grounded-thread-replay-1.0"
SOURCE = {
    "messages": [
        {
            "message_id": "synthetic-message",
            "from_addr": "Cedar Orders",
            "sent_at": "2026-09-20T08:00:00+00:00",
            "body": "Order no. 7842. Mushroom bowl with brown rice.\n"
            "Total (incl. GST)\n$18.60\nPaid by card.\n"
            "Pickup time 6:20 PM at City Campus.\n"
            "Order received; collection is not confirmed.\n"
            "This is an automated message; no reply needed.",
        }
    ],
    "omitted_messages": 0,
    "truncated_messages": 0,
}
ENVELOPE = {
    "to": ["qa@example.test"],
    "cc": [],
    "bcc": [],
    "reply_message_id": "synthetic-message",
    "reply": {
        "subject": "Re: Hola 👋 Order 🧾",
        "rfc_message_id": "<original@example.test>",
        "gmail_thread_id": "synthetic-thread",
    },
}
INSTRUCTIONS = {
    "summary": (
        "Summarise this thread. Include the order number, item, total paid, "
        "pickup location and stated pickup time. "
        "Do not invent completed collection or new tasks."
    ),
    "reply": (
        "Draft a short reply thanking Cedar for the order confirmation. "
        "Mention the order number and total paid. Do not claim that I collected it. "
        "No signature. Do not send."
    ),
    "answer": "How much did I pay for this order?",
    "absent": "What is the courier tracking number for this order?",
}


def assets():
    r = routing.release_manifest()
    return {
        "replay": VERSION,
        "cases_hash": digest([SOURCE, ENVELOPE, INSTRUCTIONS]),
        "routing_release": {
            k: r[k]
            for k in (
                "workflow",
                "router_version",
                "routing_prompt_hash",
                "routing_schema_hash",
                "draft_release",
                "draft_prompt_hash",
                "reply_prompt_hash",
                "draft_schema_hash",
                "reply_schema_hash",
                "grounded_answer",
            )
        },
        "summary_contract": summary_quality.contract_hash(),
        "summary_release": summary_quality.RELEASE,
    }


async def run(model):
    results = []
    for kind, hint, operation in [
        ("summary", "summarise", "summarise_thread"),
        ("reply", "reply", "draft_reply"),
        ("answer", "other", "lookup_entity"),
    ]:
        for repeat in range(2):
            route = await routing.route_request(
                INSTRUCTIONS[kind],
                hint,
                "synthetic-context",
                SOURCE,
                model,
                draft_input=ENVELOPE if kind == "reply" else None,
            )
            d = route["decision"]
            assert d["status"] == "ready" and d["operations"] == [operation], (
                kind,
                d["status"],
                d["operations"],
            )
            assert routing.dispatch_outcome(
                route, SOURCE, ENVELOPE if kind == "reply" else None
            ) == (None, None)
            results.append({"case": kind, "repeat": repeat, "passed": True})
    text, info = await model.generate(
        summary_quality.make_prompt(SOURCE, INSTRUCTIONS["summary"]), max_tokens=1800
    )
    summary = summary_quality.make_artifact(text, "synthetic-context", SOURCE)
    overview = summary["content"]["overview"].casefold()
    assert all(word in overview for word in ("7842", "18.60", "6:20", "city campus", "mushroom"))
    assert not summary["content"]["actions"] and not summary["content"]["open_questions"]
    text, _ = await model.generate(
        drafting.make_prompt(INSTRUCTIONS["reply"], SOURCE, ENVELOPE, "reply"), max_tokens=2500
    )
    claim = SimpleNamespace(
        snapshot=SOURCE,
        context_id="synthetic-context",
        draft_input=ENVELOPE,
        task_id="synthetic-task",
        instruction=INSTRUCTIONS["reply"],
    )
    draft = drafting.make_artifact(text, claim, "reply")
    assert draft["content"]["subject"] == ENVELOPE["reply"]["subject"]
    assert all(word in draft["content"]["body"] for word in ("7842", "18.60"))
    assert not draft["content"]["unresolved_fields"]
    for case in ("answer", "absent"):
        text, _ = await model.generate(
            grounded_answer.make_prompt(SOURCE, INSTRUCTIONS[case]), max_tokens=1000
        )
        try:
            answer = grounded_answer.make_artifact(text, "synthetic-context", SOURCE)
        except ValueError:
            print("SYNTHETIC_ANSWER_REJECTED", case, repr(text), flush=True)
            raise
        assert answer["content"]["found"] == (case == "answer")
        if case == "answer":
            assert "18.60" in answer["content"]["text"]
    # Reply policy changes must not contaminate the previously verified compose style.
    from app.planner.evaluate_routing import COMPOSE

    compose_envelope = {"to": ["qa@example.test"], "cc": [], "bcc": [], "reply": None}
    for _repeat in range(2):
        text, _ = await model.generate(
            drafting.make_prompt(COMPOSE, None, compose_envelope, "new"), max_tokens=2500
        )
        compose_claim = SimpleNamespace(
            snapshot=None,
            context_id=None,
            draft_input=compose_envelope,
            task_id="synthetic-compose",
            instruction=COMPOSE,
        )
        composed = drafting.make_artifact(text, compose_claim, "new")["content"]
        body = composed["body"].casefold()
        assert all(word in body for word in ("thank", "update", "review", "tomorrow"))
        assert not any(
            term in body for term in ("best regards", "sincerely", "feedback", "get back")
        )
        assert not composed["unresolved_fields"]
    return {
        **assets(),
        "provider": info.provider,
        "model": info.model.split("/")[-1],
        "routes": results,
        "summary_passed": True,
        "reply_passed": True,
        "answer_passed": True,
        "absent_fact_passed": True,
        "compose_repeats_passed": 2,
        "mailbox_read": False,
        "external_actions": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(assets(), indent=2))
        return
    s = get_settings()
    if s.inference_provider != "bedrock" or s.email_writes_enabled or s.calendar_writes_enabled:
        raise SystemExit("Requires Bedrock and disabled external writes")
    print(json.dumps(asyncio.run(run(get_model_client())), indent=2))


if __name__ == "__main__":
    main()
