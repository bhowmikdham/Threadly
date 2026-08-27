import pytest
from threadly_common.draft_state import (
    IllegalTransition, DraftStatus, assert_transition, can_transition,
)


def test_happy_path():
    path = [DraftStatus.GENERATED, DraftStatus.EDITED, DraftStatus.APPROVED, DraftStatus.SENDING, DraftStatus.SENT]
    for current, target in zip(path, path[1:]):
        assert can_transition(current, target)


def test_llm_output_cannot_jump_to_sent():
    assert not can_transition(DraftStatus.GENERATED, DraftStatus.SENDING)
    assert not can_transition(DraftStatus.GENERATED, DraftStatus.SENT)
    assert not can_transition(DraftStatus.EDITED, DraftStatus.SENT)


def test_sent_is_terminal():
    for target in DraftStatus:
        assert not can_transition(DraftStatus.SENT, target)


def test_failed_can_retry_or_discard():
    assert can_transition(DraftStatus.FAILED, DraftStatus.SENDING)
    assert can_transition(DraftStatus.FAILED, DraftStatus.DISCARDED)


def test_assert_raises():
    with pytest.raises(IllegalTransition):
        assert_transition(DraftStatus.DISCARDED, DraftStatus.APPROVED)
