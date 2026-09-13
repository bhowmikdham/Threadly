"""Rules, policy validation and API seams. Fake model outputs are not AI accuracy evidence."""

import json
from pathlib import Path

import jwt
import pytest
from pydantic import ValidationError

from app.api.errors import ApiError
from app.config import get_settings
from app.model_client.client import GenResult
from app.model_client.providers import ProviderError
from app.planner.intent_router import parse_decision, preview_route
from app.schemas.assistant import AssistantRequest, RouteDecision, RoutePreviewRequest

PLAYBOOK = Path(__file__).resolve().parents[2] / "docs" / "implementation-playbook"


def proposal(**changes):
    value = {
        "schema_version": "1.0",
        "status": "ready",
        "intent": "other",
        "output_kind": "answer",
        "operations": ["help"],
        "context_snapshot_id": None,
        "parameters": {
            "date_phrase": None,
            "time_phrase": None,
            "duration_minutes": None,
            "slot_count": None,
            "tone": None,
            "recipient_refs": [],
        },
        "missing_fields": [],
        "clarification": None,
        "rationale": "Supported request.",
        "requested_action": "none",
    }
    value.update(changes)
    return value


class FakeModel:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        return json.dumps(self.value), GenResult("fake", "synthetic")


@pytest.mark.parametrize(
    ("instruction", "intent", "operations", "missing"),
    [
        ("Summarise this thread", "summarise", ["summarise_thread"], ["source_context"]),
        ("Draft a reply", "reply", ["draft_reply"], ["reply_target"]),
        ("Write an email", "compose", ["draft_new"], ["recipient"]),
        (
            "Turn these requests into a work plan",
            "plan_schedule",
            ["plan_actions"],
            ["source_context"],
        ),
        ("What can you do?", "other", ["help"], []),
        ("Draft a reply; do not send it", "reply", ["draft_reply"], ["reply_target"]),
    ],
)
async def test_exact_commands_cover_all_five_intents(instruction, intent, operations, missing):
    model = FakeModel(error=AssertionError("rule should not need a model"))
    result = await preview_route(RoutePreviewRequest(instruction=instruction), model)
    assert (result.decision.intent, result.decision.operations) == (intent, operations)
    assert result.decision.missing_fields == missing
    assert result.decision.requested_action == "none"
    assert not result.execution_ready
    assert result.source == "rule"
    assert model.calls == []


async def test_compound_reply_keeps_calendar_dependency_and_missing_fields():
    value = proposal(
        intent="plan_schedule", output_kind="draft", operations=["suggest_slots", "draft_reply"]
    )
    value["parameters"].update(date_phrase="next week", slot_count=3)
    model = FakeModel(value)
    result = await preview_route(
        RoutePreviewRequest(
            instruction="Reply with three times I am free next week", intent_hint="reply"
        ),
        model,
    )
    assert result.decision.intent == "plan_schedule"
    assert result.decision.operations == ["suggest_slots", "draft_reply"]
    assert result.decision.status == "needs_clarification"
    assert result.decision.missing_fields == ["reply_target", "timezone", "duration_minutes"]
    assert result.decision.parameters.date_phrase == "next week"
    assert model.calls[0][1] == {"small": True, "max_tokens": 1200}
    assert "NO mailbox text" in model.calls[0][0]


async def test_summary_reply_is_not_captured_by_summary_rule():
    model = FakeModel(
        proposal(
            intent="summarise", output_kind="draft", operations=["summarise_thread", "draft_reply"]
        )
    )
    result = await preview_route(
        RoutePreviewRequest(instruction="Summarise and draft a reply"), model
    )
    assert result.source == "model"
    assert result.decision.operations == ["summarise_thread", "draft_reply"]


async def test_conflicting_hint_does_not_force_rule():
    model = FakeModel(
        proposal(
            status="needs_clarification",
            output_kind="clarification",
            operations=[],
            missing_fields=["intent"],
            clarification="Summarise or compose?",
        )
    )
    result = await preview_route(
        RoutePreviewRequest(instruction="Summarise this", intent_hint="compose"), model
    )
    assert result.source == "model" and result.decision.status == "needs_clarification"


async def test_ambiguous_time_has_no_invented_date_or_zone():
    value = proposal(
        intent="plan_schedule", output_kind="schedule_options", operations=["check_time"]
    )
    value["parameters"].update(date_phrase="tomorrow", time_phrase="4")
    result = await preview_route(
        RoutePreviewRequest(instruction="Are you free at 4 tomorrow?"), FakeModel(value)
    )
    assert result.decision.missing_fields == ["timezone", "duration_minutes", "am_or_pm"]
    assert result.decision.parameters.date_phrase == "tomorrow"


@pytest.mark.parametrize(
    ("intent", "action"), [("reply", "send_email"), ("plan_schedule", "create_event")]
)
async def test_action_is_only_a_proposal_requiring_saved_target(intent, action):
    result = await preview_route(
        RoutePreviewRequest(instruction="Execute my selected action"),
        FakeModel(proposal(intent=intent, operations=[], requested_action=action)),
    )
    assert result.decision.missing_fields == ["saved_action_target"]
    assert not result.execution_ready


async def test_unsupported_work_has_no_operations():
    model = FakeModel(proposal(status="unsupported", operations=[]))
    result = await preview_route(RoutePreviewRequest(instruction="Delete all my old emails"), model)
    assert result.decision.status == "unsupported"
    assert result.decision.operations == []


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": 100},
        {"operations": ["send_email"]},
        {"operations": ["help"] * 5},
        {"operations": ["help", "help"]},
        {"context_snapshot_id": "another-users-context"},
        {"status": "ready", "missing_fields": ["source"]},
        {"status": "needs_clarification", "clarification": None},
        {"status": "unsupported", "requested_action": "send_email"},
        {"intent": "reply", "operations": ["suggest_slots", "draft_reply"]},
        {"intent": "summarise", "operations": ["draft_reply", "summarise_thread"]},
        {"operations": []},
        {"intent": "compose", "operations": ["help"]},
    ],
)
def test_invalid_or_unbound_model_routes_rejected(changes):
    with pytest.raises((ValidationError, ValueError)):
        parse_decision(json.dumps(proposal(**changes)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_minutes", "30"),
        ("duration_minutes", True),
        ("duration_minutes", 4),
        ("slot_count", 4),
        ("recipient_refs", ["invented-contact"]),
    ],
)
def test_parameter_types_ranges_and_references_are_strict(field, value):
    decision = proposal()
    decision["parameters"][field] = value
    with pytest.raises((ValidationError, ValueError)):
        parse_decision(json.dumps(decision))


@pytest.mark.parametrize(
    "text", ["```json\n{}\n```", '{"intent":"other","intent":"reply"}', "x" * 16001, "null", "[]"]
)
def test_malformed_json_is_not_repaired_or_guessed(text):
    with pytest.raises((ValidationError, ValueError)):
        parse_decision(text)


async def test_invalid_model_response_has_sanitized_error_without_retry():
    model = FakeModel({"private": "sensitive@example.test"})
    with pytest.raises(ApiError) as exc:
        await preview_route(RoutePreviewRequest(instruction="Something novel"), model)
    assert exc.value.status == 502 and exc.value.code == "invalid_route_output"
    assert len(model.calls) == 1 and exc.value.detail is None


async def test_upstream_failure_does_not_become_successful_other_route():
    with pytest.raises(ApiError) as exc:
        await preview_route(
            RoutePreviewRequest(instruction="Something novel"),
            FakeModel(error=ProviderError("secret error")),
        )
    assert exc.value.status == 503
    assert "secret" not in exc.value.message


def test_runtime_contracts_accept_shared_examples():
    AssistantRequest.model_validate_json((PLAYBOOK / "examples/request.json").read_text())
    RouteDecision.model_validate_json((PLAYBOOK / "examples/route.json").read_text())


def test_preview_api_authentication_and_read_only_shape(client, auth_headers):
    assert client.post("/assistant/route-preview", json={"instruction": "Help"}).status_code == 401
    result = client.post(
        "/assistant/route-preview", json={"instruction": "Help"}, headers=auth_headers(1)
    )
    assert result.status_code == 200
    assert result.json()["execution_ready"] is False
    assert result.json()["decision"]["intent"] == "other"
    assert "task_id" not in result.json()


@pytest.mark.parametrize(
    "body",
    [
        {"instruction": " "},
        {"instruction": "a" * 8001},
        {"instruction": "Help", "user_id": 2},
        {"instruction": "Help", "context_snapshot_id": "x"},
        {"instruction": "Help", "continuation": {"task_id": "x"}},
        {"instruction": "Help", "intent_hint": "send_email"},
    ],
)
def test_preview_api_rejects_untrusted_fields(client, auth_headers, body):
    result = client.post("/assistant/route-preview", json=body, headers=auth_headers(1))
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "validation_error"
    assert all(set(e) == {"loc", "type", "msg"} for e in result.json()["error"]["detail"])


@pytest.mark.parametrize("payload", [{}, {"sub": "bad"}, {"sub": "0"}, {"sub": "-1"}])
def test_invalid_subject_is_401_not_500(client, payload):
    settings = get_settings()
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    result = client.post(
        "/assistant/route-preview",
        json={"instruction": "Help"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 401
