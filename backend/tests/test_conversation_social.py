"""Natural social turns are read-only and never reset a pending user goal."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.conversation import engine, social
from tests.test_conversation import Model, tool


@pytest.mark.parametrize(
    "text",
    [
        "Hey Threadly, how are you?",
        "Hi!",
        "Hello Threadly",
        "How’s it going?",
        "Thanks Threadly",
        "No thanks",
        "Bye",
    ],
)
async def test_clear_social_turns_use_no_model_or_tools_and_keep_owned_context(text):
    state = {
        "refs": {"selected": {"thread_id": "owned-thread"}},
        "active_goal": "reply",
        "history": [],
    }
    before = deepcopy(state)
    runtime = SimpleNamespace(state=state, request=SimpleNamespace(instruction=text))
    result = await engine.run({"user_turn": text, "recent_dialogue": []}, runtime, Model())
    assert result["kind"] == "message"
    assert len(result["text"]) < 120
    assert "Threadly" not in result["text"]
    assert not any(
        word in result["text"].lower() for word in ("checking", "saved", "sent", "booked", "done")
    )
    assert state == before


def test_followup_variety_and_explicit_identity_without_context_reset():
    first = social.reply({"user_turn": "Hey Threadly, how are you?"})
    history = [{"user": "Hey Threadly, how are you?", "assistant": first["text"]}]
    followup = social.reply({"user_turn": "I'm good, thanks", "recent_dialogue": history})
    assert followup and "Glad to hear" in followup["text"]
    again = social.reply({"user_turn": "Hey Threadly, how are you?", "recent_dialogue": history})
    assert again["text"] != first["text"]
    assert "Threadly" not in again["text"]
    assert "Threadly" in social.reply({"user_turn": "Who are you?"})["text"]


@pytest.mark.parametrize(
    "text",
    [
        "Hey Threadly, how are you? Please draft a reply to this email.",
        "Hi, 请总结我的邮件",
        "The email says hello",
        "Cancel, cancel",
        "Stop working on that",
        "Actually, use tomorrow",
        "Yes, create the event",
        "Good",
        "Fine thanks",
    ],
)
def test_work_corrections_cancellations_and_ambiguous_answers_keep_normal_routing(text):
    assert social.reply({"user_turn": text}) is None


@pytest.mark.parametrize(
    "pending",
    [
        {"pending_email_draft": {"status": "clarification", "recipient": "Alex"}},
        {
            "active_work": {
                "state": "needs_clarification",
                "question": {"prompt": "What should it say?"},
            }
        },
        {"pending_calendar_event": {"title": "", "status": "clarification"}},
    ],
)
async def test_a_greeting_can_be_the_requested_body_or_title(pending):
    context = {"user_turn": "Hello", **pending}
    assert social.reply(context) is None

    class Runtime:
        evidence = {}

    model = Model(tool("respond", kind="clarification", text="Should the message say just Hello?"))
    response = await engine.run(context, Runtime(), model)
    assert model.contexts[0] == context
    assert response["text"] == "Should the message say just Hello?"


def test_social_detour_preserves_pending_clarification_without_claiming_it_finished():
    context = {
        "user_turn": "Thanks",
        "pending_email_draft": {"recipient": "Alex", "status": "clarification"},
    }
    before = deepcopy(context)
    assert social.reply(context) is None
    assert context == before
