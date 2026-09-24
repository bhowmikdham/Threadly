"""Deterministic handling for provider-split compound workflow decisions."""

from copy import deepcopy

from app.conversation import engine


def decision(*calls):
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": f"call-{index}",
                    "name": name,
                    "input": values,
                }
            }
            for index, (name, values) in enumerate(calls, 1)
        ],
    }


class Model:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.inputs = []

    async def decide(self, system, messages, tools):
        self.inputs.append(deepcopy(messages))
        return self.messages.pop(0)


class Runtime:
    def __init__(self):
        self.calls = []
        self.evidence = {}

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "read_email":
            self.evidence[arguments.reference] = "Could we meet tomorrow?"
            return {"reference": arguments.reference, "body": self.evidence[arguments.reference]}
        assert name == "prepare_workflow"
        return {
            "kind": "proposal" if arguments.compound else "task",
            "text": "Here is the proposed work for review.",
        }


async def test_provider_split_workflows_become_one_compound_proposal():
    model = Model(
        decision(("read_email", {"reference": "selected"})),
        decision(
            ("prepare_workflow", {"intent": "summarise", "reference": "selected"}),
            ("prepare_workflow", {"intent": "plan_schedule", "reference": "selected"}),
            ("prepare_workflow", {"intent": "reply", "reference": "selected"}),
        ),
    )
    runtime = Runtime()

    result = await engine.run(
        {
            "user_turn": (
                "Summarise this email, suggest three slots tomorrow, and draft a reply "
                "with those times"
            )
        },
        runtime,
        model,
    )

    assert result["kind"] == "proposal"
    assert [name for name, _ in runtime.calls] == ["read_email", "prepare_workflow"]
    workflow = runtime.calls[-1][1]
    assert workflow.intent == "plan_schedule"
    assert workflow.compound is True
    assert workflow.reference == "selected"
    assert result["trace"] == [
        {"tool": "read_email", "status": "ok"},
        {"tool": "prepare_workflow", "status": "ok"},
    ]


async def test_non_calendar_merge_preserves_draft_intent_and_recipient_roles():
    runtime = Runtime()
    result = await engine.run(
        {},
        runtime,
        Model(
            decision(
                ("prepare_workflow", {"intent": "summarise", "reference": "selected"}),
                (
                    "prepare_workflow",
                    {
                        "intent": "reply",
                        "reference": "selected",
                        "to_refs": ["recipient-1"],
                        "cc_refs": ["recipient-2"],
                    },
                ),
            )
        ),
    )

    assert result["kind"] == "proposal"
    workflow = runtime.calls[0][1]
    assert workflow.intent == "reply" and workflow.compound is True
    assert workflow.to_refs == ["recipient-1"]
    assert workflow.cc_refs == ["recipient-2"]


async def test_conflicting_source_references_fail_closed_and_return_to_model():
    runtime = Runtime()
    model = Model(
        decision(
            ("prepare_workflow", {"intent": "summarise", "reference": "selected"}),
            ("prepare_workflow", {"intent": "reply", "reference": "mail-1"}),
        ),
        decision(
            (
                "respond",
                {
                    "kind": "clarification",
                    "text": "Which email should I use for the combined request?",
                },
            )
        ),
    )

    result = await engine.run({}, runtime, model)

    assert result["kind"] == "clarification"
    assert runtime.calls == []
    assert result["trace"][0] == {"tool": "prepare_workflow", "status": "invalid"}
    feedback = model.inputs[1][-1]["content"]
    assert len(feedback) == 2
    assert all(
        "one source reference" in item["toolResult"]["content"][0]["json"]["message"]
        for item in feedback
    )
