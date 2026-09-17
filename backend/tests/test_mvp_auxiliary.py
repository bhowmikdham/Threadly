"""Reproducible auxiliary contracts and pinned Flow adapter dispatch; no AWS calls."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.assistant import coordinator, planning
from app.assistant.summary import digest
from app.config import get_settings
from app.schemas.meeting_response import ExtractedChoice
from app.workflows import auxiliary, mvp_assets, registry
from app.workflows.bedrock_flows import FlowResult
from app.workflows.release import template


def test_exported_assets_match_runtime():
    stored = Path(__file__).parents[1] / "fixtures/mvp/prompts-v1.json"
    assert json.loads(stored.read_text()) == mvp_assets.assets()


def flow(alias="ALIAS00001"):
    profile = f"arn:aws:bedrock:ap-southeast-2:123456789012:inference-profile/{registry.PROFILE}"
    return registry.FlowEntry(
        implementation="bedrock_flow",
        region="ap-southeast-2",
        flow_arn="arn:aws:bedrock:ap-southeast-2:123456789012:flow/FLOW000001",
        alias_id=alias,
        version="1",
        model_profile_arn=profile,
        execution_role_arn="arn:aws:iam::123456789012:role/runtime",
        definition_hash=digest(registry.flow_definition(profile)),
    )


async def test_flow_is_pinned_after_registry_change(monkeypatch):
    monkeypatch.setattr(get_settings(), "inference_provider", "bedrock")
    original = auxiliary.Manifest(
        schema_version="1.0", operations={op: flow() for op in auxiliary.OPERATIONS}
    )
    monkeypatch.setattr(
        get_settings(), "assistant_auxiliary_workflow_manifest", original.model_dump_json()
    )
    saved = auxiliary.release()
    replacement = auxiliary.Manifest(
        schema_version="1.0", operations={op: flow("ALIAS00002") for op in auxiliary.OPERATIONS}
    )
    monkeypatch.setattr(
        get_settings(), "assistant_auxiliary_workflow_manifest", replacement.model_dump_json()
    )

    class Invoker:
        async def invoke(self, entry, prompt):
            assert entry.alias_id == "ALIAS00001"
            return FlowResult(
                '{"status":"ambiguous","option":null,"quote":""}', {"provider": "fixture"}
            )

    result, _ = await auxiliary.generate(
        "interpret_meeting_response", "fixture", saved, flow_invoker=Invoker()
    )
    assert ExtractedChoice.model_validate_json(result).status == "ambiguous"
    resources = template(flow().model_profile_arn, operations=auxiliary.OPERATIONS)["Resources"]
    assert len([v for v in resources.values() if v["Type"] == "AWS::Bedrock::Flow"]) == 3


def test_native_configuration_change_requires_replan(monkeypatch):
    saved = auxiliary.release()
    monkeypatch.setattr(get_settings(), "model_main", "changed")
    with pytest.raises(ApiError):
        auxiliary.validate(saved)


@pytest.mark.parametrize("change", ["owner", "date", "quote", "source", "cycle", "duplicate"])
def test_plan_evidence_replay_rejects_unsupported_claims(change):
    claim = SimpleNamespace(
        task_id="synthetic",
        context_id="synthetic",
        snapshot={"messages": [{"message_id": "m1", "body": "Alex will review on 2026-10-05."}]},
    )
    item = {
        "text": "Review",
        "owner": "Alex",
        "due_date": "2026-10-05",
        "sources": [1],
        "quote": "Alex will review on 2026-10-05.",
        "depends_on": [],
    }
    replacements = {
        "owner": ("owner", "Sam"),
        "date": ("due_date", "2026-10-06"),
        "quote": ("quote", "Approve everything"),
        "source": ("sources", [2]),
        "cycle": ("depends_on", [1]),
        "duplicate": ("sources", [1, 1]),
    }
    valid = planning.make_artifact(json.dumps({"items": [item], "open_questions": []}), claim)
    assert valid["content"]["accepted_item_ids"] == []
    field, value = replacements[change]
    item[field] = value
    with pytest.raises(ValueError):
        planning.make_artifact(json.dumps({"items": [item], "open_questions": []}), claim)


def test_master_does_not_accept_dropped_words():
    output = {
        "command": {
            "clauses": [{"start": 1, "end": 1, "kind": "requested", "operations": ["summary"]}],
            "summary_usage": "not_applicable",
            "lookup_query": None,
            "ambiguities": [],
        },
        "scheduling": None,
        "other_operation": None,
    }
    with pytest.raises(ValueError):
        coordinator.parse(json.dumps(output), "Summarise but never send")
