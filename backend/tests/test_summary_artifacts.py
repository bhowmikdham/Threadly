"""Synthetic output validation. Does not assert live model quality."""

import json

import pytest

from app.assistant.summary import make_artifact

SNAPSHOT = {
    "messages": [
        {"message_id": "m1", "body": "Reply Friday.", "from_addr": "sender", "sent_at": None}
    ],
    "omitted_messages": 0,
    "truncated_messages": 0,
}


@pytest.mark.parametrize("sources", [[2], [0], [-1], [1, 1], [True], ["1"], []])
def test_sources_must_reference_actual_snapshot_positions(sources):
    value = {
        "overview": "A reply is due Friday.",
        "decisions": [],
        "actions": [{"text": "Reply Friday", "sources": sources}],
        "open_questions": [],
    }
    with pytest.raises(ValueError):
        make_artifact(json.dumps(value), "context", SNAPSHOT)


@pytest.mark.parametrize(
    "text", ['{"overview":"one","overview":"two"}', "x" * 16001, "```json\n{}\n```"]
)
def test_malformed_or_oversized_output_is_not_repaired(text):
    with pytest.raises(ValueError):
        make_artifact(text, "context", SNAPSHOT)


def test_model_cannot_confirm_actions_or_supply_external_ids():
    value = {
        "overview": "Reply Friday",
        "decisions": [],
        "actions": [{"text": "Reply", "sources": [1], "confirmation": "user_confirmed"}],
        "open_questions": [],
    }
    with pytest.raises(ValueError):
        make_artifact(json.dumps(value), "context", SNAPSHOT)
