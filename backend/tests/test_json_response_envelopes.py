"""Presentation tolerance must never change source, recipient or action validation."""

import json
from types import SimpleNamespace

import pytest

from app.assistant import drafting, reads
from app.model_client.structured import json_object
from app.planner.intent_router import parse_decision
from tests.test_intent_router import proposal


@pytest.mark.parametrize(
    "wrap", [lambda s: s, lambda s: "```json\n" + s + "\n```", lambda s: "```\r\n" + s + "\r\n```"]
)
def test_raw_or_single_complete_fence_with_same_semantics(wrap):
    value = proposal(intent="compose", output_kind="draft", operations=["draft_new"])
    assert parse_decision(wrap(json.dumps(value))).model_dump() == value
    value["parameters"]["recipient_refs"] = ["Alex"]
    with pytest.raises(ValueError):
        parse_decision(wrap(json.dumps(value)))


@pytest.mark.parametrize(
    "value",
    [
        'Explanation\n{"ok":true}',
        '{"ok":true}\nExplanation',
        '```json\n{"ok":true}\n```\nExtra',
        '```json\n{"ok":true}',
        '```javascript\n{"ok":true}\n```',
        '{"a":1}{"a":2}',
        '```json\n{"a":1}\n```\n```json\n{"a":2}\n```',
        '{"intent":"compose","intent":"reply"}',
        '```json\n{"a":1,"a":2}\n```',
        '{"a":{"b":1,"b":2}}',
        '{"a":NaN}',
        '{"a":Infinity}',
        "[]",
        "null",
    ],
)
def test_ambiguous_malformed_duplicate_and_nonfinite_output_rejected(value):
    with pytest.raises(ValueError):
        json_object(value, max_chars=16000)


def test_fenced_draft_keeps_backend_envelope_and_source_checks():
    envelope = {"to": ["qa@example.test"], "cc": [], "bcc": [], "reply": None}
    claim = SimpleNamespace(
        snapshot=None,
        context_id=None,
        draft_input=envelope,
        task_id="test",
        instruction="Write a thank-you email",
    )
    value = {"subject": "Thanks", "body": "Thank you.", "unresolved_fields": [], "sources": []}
    artifact = drafting.make_artifact("```json\n" + json.dumps(value) + "\n```", claim, "new")
    assert artifact["content"]["body"] == "Thank you."
    for invalid in ({**value, "to": ["attacker@example.test"]}, {**value, "sources": [1]}):
        with pytest.raises(ValueError):
            drafting.make_artifact("```json\n" + json.dumps(invalid) + "\n```", claim, "new")


def test_help_describes_current_workflows_without_promising_write_access():
    text = reads.help_artifact()["content"]["text"]
    assert "bounded Gmail" in text and "Calendar" in text
    assert "separate approval" in text and "enabled server controls" in text
    assert "No mailbox import" in text
    assert "not available in this release" not in text


def test_live_replay_pins_current_prompt_schemas_and_cases():
    from pathlib import Path

    from app.assistant import routing
    from app.assistant.summary import digest
    from app.planner.evaluate_routing import CASES, REPLAY_VERSION

    evidence = json.loads(
        (Path(__file__).parents[2] / "docs/evaluation/compose-routing-live-v1.json").read_text()
    )
    assert evidence["replay"] == REPLAY_VERSION
    assert evidence["cases_hash"] == digest(CASES)
    current = routing.release_manifest()
    # Deployment-specific model/configuration hashes differ in offline fixtures.
    for key in (
        "router_version",
        "routing_prompt_hash",
        "routing_schema_hash",
        "draft_release",
        "draft_prompt_hash",
        "draft_schema_hash",
    ):
        assert evidence["routing_release"][key] == current[key]
    assert len(evidence["routes"]) == 7 and all(r["passed"] for r in evidence["routes"])
    assert evidence["draft_passed"]
    assert not evidence["external_actions"] and not evidence["mailbox_read"]
