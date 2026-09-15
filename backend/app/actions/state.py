"""The action lifecycle is independent of generation task success."""

STATES = (
    "proposed",
    "approved",
    "executing",
    "outcome_unknown",
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
    "expired",
    "superseded",
)
TRANSITIONS = {
    "proposed": {"approved", "rejected", "cancelled", "expired", "superseded"},
    "approved": {"executing", "cancelled", "expired", "superseded"},
    "executing": {"outcome_unknown", "succeeded", "failed"},
    "outcome_unknown": {"succeeded", "failed"},
}
TERMINAL = set(STATES) - set(TRANSITIONS)
PRE_DISPATCH = {"proposed", "approved"}


def allows(before: str, after: str) -> bool:
    return after in TRANSITIONS.get(before, set())
