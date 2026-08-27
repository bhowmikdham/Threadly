"""Draft lifecycle state machine. The only path to an outbound email is
generated/edited -> approved -> sending -> sent, and the approved step is a
human click — the LLM can never cross it."""
from enum import Enum


class DraftStatus(str, Enum):
    GENERATED = "generated"
    EDITED = "edited"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DISCARDED = "discarded"


TRANSITIONS: dict[DraftStatus, set[DraftStatus]] = {
    DraftStatus.GENERATED: {DraftStatus.EDITED, DraftStatus.APPROVED, DraftStatus.DISCARDED},
    DraftStatus.EDITED: {DraftStatus.EDITED, DraftStatus.APPROVED, DraftStatus.DISCARDED},
    DraftStatus.APPROVED: {DraftStatus.EDITED, DraftStatus.SENDING, DraftStatus.DISCARDED},
    DraftStatus.SENDING: {DraftStatus.SENT, DraftStatus.FAILED},
    DraftStatus.FAILED: {DraftStatus.SENDING, DraftStatus.DISCARDED},
    DraftStatus.SENT: set(),
    DraftStatus.DISCARDED: set(),
}


class IllegalTransition(Exception):
    def __init__(self, current: DraftStatus, target: DraftStatus):
        super().__init__(f"cannot move draft from {current.value} to {target.value}")
        self.current = current
        self.target = target


def can_transition(current: DraftStatus, target: DraftStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: DraftStatus, target: DraftStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
