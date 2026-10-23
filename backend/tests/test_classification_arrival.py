"""Synthetic metadata boundaries: no security inference or new source/provider call."""

import copy
from datetime import UTC, datetime

import pytest

from app.classification import service
from app.classification.contracts import ClassificationResponse
from tests.test_classification import configured, invoke, pipeline, source  # noqa: F401


def test_provider_timestamp_and_identity_only():
    message = source()["messages"][0]
    result = service.latest_incoming([message])
    assert result.model_dump() == {
        "message_id": "def456", "received_at": datetime(2026, 10, 6, 1, tzinfo=UTC),
        "timestamp_source": "gmail.internalDate",
    }
    assert "subject" not in result.model_dump_json()


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(received_at=None),
    lambda m: m.update(received_at="2026-10-06T01:00:00"),
    lambda m: m.update(received_at="invalid"),
    lambda m: m.update(gmail_msg_id="not-an-id"),
    lambda m: m.update(is_from_user=True),
    lambda m: m["reply_metadata"].update(label_ids=["SENT", "INBOX"]),
    lambda m: m["reply_metadata"].update(label_ids=["DRAFT", "INBOX"]),
    lambda m: m["reply_metadata"].update(label_ids=["TRASH", "INBOX"]),
    lambda m: m["reply_metadata"].update(label_ids=[]),
])
def test_unreliable_or_outgoing_metadata_fails_closed(mutation):
    message = source()["messages"][0]
    mutation(message)
    assert service.latest_incoming([message]) is None


def test_new_message_in_old_thread_and_ambiguous_order():
    old = source()["messages"][0]
    new = copy.deepcopy(old)
    new.update(gmail_msg_id="aaa123", received_at="2026-10-23T01:00:00Z")
    assert service.latest_incoming([new, old]).message_id == "aaa123"
    old["received_at"] = new["received_at"]
    assert service.latest_incoming([old, new]) is None
    old["received_at"] = None
    assert service.latest_incoming([old, new]) is None


async def test_existing_read_exposes_metadata_without_extra_provider_work(pipeline):  # noqa: F811
    _, provider, reads = pipeline
    result = await invoke()
    assert result.latest_incoming.message_id == "def456"
    assert len(reads) == 2 and len(provider.calls) == 1
    assert result.persisted is False
    old_client = result.model_dump()
    old_client.pop("latest_incoming")
    assert ClassificationResponse.model_validate(old_client).latest_incoming is None
