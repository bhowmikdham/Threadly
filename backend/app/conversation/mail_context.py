"""Reference-only conversation memory, independent of transient search-card ordinals."""

from copy import deepcopy

LIMIT = 8


def retain(state, reference, scope):
    source = state["refs"][reference]
    order = state.setdefault("context_order", [])
    identity = (source.get("thread_id"), source.get("message_id"), source.get("context_id"))
    for key in order:
        item = state["refs"].get(key, {})
        if (item.get("thread_id"), item.get("message_id"), item.get("context_id")) == identity:
            item["remembered_scope"] = scope
            return key
    number = state.get("next_context_reference", 1)
    key = f"context-{number}"
    state["next_context_reference"] = number + 1
    state["refs"][key] = deepcopy(source)
    state["refs"][key]["remembered_scope"] = scope
    order.append(key)
    while len(order) > LIMIT:
        state["refs"].pop(order.pop(0), None)
    return key


def reset_search(state):
    keep = {"selected", *state.get("context_order", [])}
    state["refs"] = {k: v for k, v in state["refs"].items() if k in keep}
    state["result_order"] = []
    state.pop("search", None)


def model_context(state):
    return [
        {
            "reference": key,
            "scope": state["refs"][key].get("remembered_scope"),
            "note": "Previously read source; read again for current evidence.",
        }
        for key in state.get("context_order", [])
        if key in state["refs"]
    ]
