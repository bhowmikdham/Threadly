"""Provisioning preserves other resources and recovers completed candidate releases."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

INFRA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRA))
SPEC = importlib.util.spec_from_file_location(
    "classification_provision", INFRA / "classification_flows.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

from app.classification.flows import definition, models, prompt_variant  # noqa: E402

ACCOUNT = "123456789012"
ARN = f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:flow/FLOW123456"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/test-classification"
MODEL = models(ACCOUNT)["haiku"]
PROMPT = f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:prompt/HAIKU12345:1"
GRAPH = definition(PROMPT)
TAGS = {"ManagedBy": "threadly-classification", "ReleaseHash": "a" * 64}


def test_render_only_never_calls_aws(tmp_path):
    result = m.run(SimpleNamespace(render_only=True, output=tmp_path),
                   lambda *a: pytest.fail("AWS call in render-only mode"))
    assert result["status"] == "render_only"
    assert result["candidates"] == {}
    assert result["backend_enabled"] is False
    assert result["selected_candidate"] == "haiku"
    assert set(result["prompts"]) == {"haiku"}
    template = json.loads((tmp_path / "template.json").read_text())
    assert set(template["Resources"]) == {"HaikuRole"}
    assert set(template["Outputs"]) == {"HaikuRoleArn"}
    assert not (tmp_path / "nova_micro.flow.json").exists()


def test_55_render_only_keeps_explicit_model_and_distinct_release(tmp_path):
    old = m.run(SimpleNamespace(render_only=True, output=tmp_path / "old"),
                lambda *a: pytest.fail("AWS call in render-only mode"))
    new = m.run(SimpleNamespace(render_only=True, output=tmp_path / "new", model=m.HAIKU_55),
                lambda *a: pytest.fail("AWS call in render-only mode"))
    assert new["release_hash"] != old["release_hash"]
    template = json.loads((tmp_path / "new" / "template.json").read_text())
    statements = template["Resources"]["HaikuRole"]["Properties"]["Policies"][0][
        "PolicyDocument"]["Statement"]
    assert statements[0]["Resource"] == [
        f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:inference-profile/au.{m.HAIKU_55}"]
    assert len(statements[1]["Resource"]) == 2
    assert all(m.HAIKU_55 in resource for resource in statements[1]["Resource"])


def test_55_profile_routing_change_rejected_before_provisioning():
    profile = {
        "status": "ACTIVE",
        "inferenceProfileArn":
            f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:inference-profile/au.{m.HAIKU_55}",
        "models": [{"modelArn": f"arn:aws:bedrock:{region}::foundation-model/{m.HAIKU_55}"}
                   for region in ("ap-southeast-2", "ap-southeast-4")],
    }
    m.provision.validate_profile(profile, ACCOUNT, model=m.HAIKU_55)
    profile["models"].append({
        "modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{m.HAIKU_55}"})
    with pytest.raises(RuntimeError, match="destinations changed"):
        m.provision.validate_profile(profile, ACCOUNT, model=m.HAIKU_55)


def test_existing_flow_reused_without_mutation():
    calls = []

    def aws(service, action, payload=None):
        calls.append(action)
        return {"list-flows": {"flowSummaries": [{"name": "candidate", "arn": ARN}]},
                "list-tags-for-resource": {"tags": TAGS},
                "get-flow": {"executionRoleArn": ROLE, "definition": GRAPH}}[action]

    assert m.ensure_flow(aws, "candidate", GRAPH, ROLE, TAGS) == ARN
    assert calls == ["list-flows", "list-tags-for-resource", "get-flow"]


@pytest.mark.parametrize("drift", ["owner", "graph", "role"])
def test_existing_flow_drift_is_not_overwritten(drift):
    def aws(service, action, payload=None):
        return {"list-flows": {"flowSummaries": [{"name": "candidate", "arn": ARN}]},
                "list-tags-for-resource": {"tags": {} if drift == "owner" else TAGS},
                "get-flow": {"executionRoleArn": "wrong" if drift == "role" else ROLE,
                             "definition": {} if drift == "graph" else GRAPH}}[action]

    with pytest.raises(RuntimeError):
        m.ensure_flow(aws, "candidate", GRAPH, ROLE, TAGS)


def test_create_flow_request_shape_and_idempotency():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    requests = []

    def aws(service, action, payload=None):
        if action == "list-flows":
            return {"flowSummaries": []}
        if action == "create-flow":
            validate_parameters(payload, Session().get_service_model("bedrock-agent")
                                .operation_model("CreateFlow").input_shape)
            requests.append(payload)
            return {"arn": ARN}
        assert action == "get-flow"
        return {"executionRoleArn": ROLE, "definition": GRAPH}

    m.ensure_flow(aws, "candidate", GRAPH, ROLE, TAGS)
    m.ensure_flow(aws, "candidate", GRAPH, ROLE, TAGS)
    assert requests[0] == requests[1]


def test_publish_resumes_existing_version_and_alias(monkeypatch):
    monkeypatch.setattr(m.provision, "prepare_flow",
                        lambda *a: {"definition_sha256": m.provision.digest(GRAPH)})

    def aws(service, action, payload=None):
        return {
            "list-flow-versions": {"flowVersionSummaries": [{"version": "1",
                "description": "classification-" + m.provision.digest(GRAPH)}]},
            "list-flow-aliases": {"flowAliasSummaries": [{"name": "candidate-v1",
                                                         "id": "ALIAS12345"}]},
            "get-flow-alias": {"arn": ARN + "/alias/ALIAS12345",
                               "routingConfiguration": [{"flowVersion": "1"}]},
            "get-flow-version": {"arn": ARN, "version": "1", "status": "Prepared",
                                 "executionRoleArn": ROLE, "definition": GRAPH},
            "get-prompt": {"arn": PROMPT, "defaultVariant": "classify",
                           "variants": [prompt_variant(MODEL)]},
        }[action]

    assert m.publish(aws, ARN, ROLE, MODEL, GRAPH, TAGS).version == "1"
