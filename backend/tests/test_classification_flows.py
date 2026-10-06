"""Flow graph, release pins, bounded event streams and model-role boundaries."""

import copy
import json
import threading

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.classification import flows, service
from app.classification.flow_release import bundle, caller_policy
from app.classification.provider import ClassificationProvider
from app.config import get_settings
from app.workflows.bedrock_flows import FlowError
from tests.test_classification import decision

ACCOUNT = "123456789012"
ARN = f"arn:aws:bedrock:{flows.REGION}:{ACCOUNT}:flow/FLOW123456"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/classification-test"
PROMPTS = {key: f"arn:aws:bedrock:{flows.REGION}:{ACCOUNT}:prompt/{identifier}:1"
           for key, identifier in (("haiku", "HAIKU12345"),)}


def target(candidate="haiku"):
    model = flows.models(ACCOUNT)[candidate]
    return flows.ClassificationFlow(flow_arn=ARN, alias_id="ALIAS12345", version="1",
                                    execution_role_arn=ROLE, model_profile_arn=model,
                                    prompt_arn=PROMPTS[candidate],
                                    definition_hash=digest(flows.definition(PROMPTS[candidate])))


class Control:
    def __init__(self, entry):
        self.entry = entry
        self.alias = {"arn": ARN + "/alias/ALIAS12345", "updatedAt": "2026-10-07",
                      "routingConfiguration": [{"flowVersion": "1"}]}
        self.version = {"arn": ARN, "version": "1", "status": "Prepared",
                        "executionRoleArn": ROLE,
                        "definition": flows.definition(entry.prompt_arn)}
        self.closed = False

    def get_flow_alias(self, **kwargs):
        assert kwargs == {"flowIdentifier": ARN, "aliasIdentifier": "ALIAS12345"}
        return copy.deepcopy(self.alias)

    def get_flow_version(self, **kwargs):
        assert kwargs == {"flowIdentifier": ARN, "flowVersion": "1"}
        return copy.deepcopy(self.version)

    def close(self):
        self.closed = True

    def get_prompt(self, **kwargs):
        return {"arn": self.entry.prompt_arn, "defaultVariant": "classify",
                "variants": [flows.prompt_variant(self.entry.model_profile_arn)]}


class Stream:
    def __init__(self, events):
        self.events, self.closed = events, False

    def __iter__(self):
        yield from self.events

    def close(self):
        self.closed = True


def events():
    return [{"flowOutputEvent": {"nodeName": "Output", "nodeType": "FlowOutputNode",
                                 "content": {"document": json.dumps(decision())}}},
            {"flowCompletionEvent": {"completionReason": "SUCCESS"}}]


class Runtime:
    def __init__(self, stream):
        self.stream, self.request, self.closed = stream, None, False

    def invoke_flow(self, **kwargs):
        self.request = kwargs
        return {"responseStream": self.stream}

    def close(self):
        self.closed = True


def provider(entry, stream=None):
    control, runtime = Control(entry), Runtime(stream or Stream(events()))
    client = ClassificationProvider(
        factory=lambda *a: pytest.fail("Converse fallback must never execute"),
        flow_factory=lambda name, _: control if name == "bedrock-agent" else runtime,
        max_concurrency=1,
    )
    return client, control, runtime


async def generate(client, entry):
    payload = {"messages": [{"subject": "Review", "body": "Contact alice@example.test",
                             "source_id": "m1", "received_at": "2026-10-07T01:23:45+00:00"}]}
    return await client.generate(service.PROMPT, payload, model=entry.model_profile_arn,
                                 region=entry.region, timeout=entry.timeout_seconds, flow=entry)


def test_api_graph_and_selected_model_iam():
    entry = target()
    graph = flows.definition(entry.prompt_arn)
    model = Session().get_service_model("bedrock-agent")
    validate_parameters({"name": "test-flow", "executionRoleArn": ROLE, "definition": graph},
                        model.operation_model("CreateFlow").input_shape)
    inline = flows.prompt_variant(entry.model_profile_arn)
    validate_parameters({"name": "test-prompt", "defaultVariant": "classify",
                         "variants": [inline]}, model.operation_model("CreatePrompt").input_shape)
    assert inline["templateType"] == "CHAT"
    assert inline["templateConfiguration"]["chat"]["system"] == [{"text": service.PROMPT}]
    assert inline["inferenceConfiguration"] == {"text": {"maxTokens": 1500}}
    assert {node["type"] for node in graph["nodes"]} == {"Input", "Prompt", "Output"}
    template, _, _, _ = bundle(ACCOUNT, PROMPTS)
    assert set(template["Resources"]) == {"HaikuRole"}  # graph and prompt are API-managed
    properties = template["Resources"]["HaikuRole"]["Properties"]
    statements = properties["Policies"][0]["PolicyDocument"]["Statement"]
    assert statements[0]["Resource"] == [entry.model_profile_arn]
    assert statements[1]["Condition"]["StringEquals"] == {
        "bedrock:InferenceProfileArn": entry.model_profile_arn}
    assert len(statements[1]["Resource"]) == 2
    assert statements[-1]["Action"] == ["bedrock:RenderPrompt"]
    assert caller_policy(entry)["Statement"][0]["Resource"] == [ARN + "/alias/ALIAS12345"]


@pytest.mark.parametrize("changes", [
    {"alias_id": "TSTALIASID"}, {"version": "DRAFT"}, {"definition_hash": "0" * 64},
    {"model_profile_arn": "amazon.nova-pro-v1:0"}, {"max_events": 1000},
    {"model_profile_arn":
     f"arn:aws:bedrock:{flows.REGION}::foundation-model/amazon.nova-micro-v1:0"},
    {"execution_role_arn": "arn:aws:iam::999999999999:role/wrong-account"},
])
def test_unpinned_or_unknown_target_rejected(changes):
    with pytest.raises(ValueError):
        flows.ClassificationFlow.model_validate({**target().model_dump(), **changes})


@pytest.mark.parametrize("change", ["alias", "definition", "role", "status"])
def test_live_target_drift_rejected(change):
    entry = target()
    control = Control(entry)
    if change == "alias":
        control.alias["routingConfiguration"][0]["flowVersion"] = "2"
    elif change == "definition":
        control.version["definition"]["nodes"][1]["configuration"]["prompt"][
            "sourceConfiguration"]["resource"]["promptArn"] = "wrong-prompt"
    elif change == "role":
        control.version["executionRoleArn"] += "other"
    else:
        control.version["status"] = "NotPrepared"
    with pytest.raises(FlowError, match="workflow_release_changed"):
        flows.verify_target(control, entry)


async def test_flow_masks_mail_but_preserves_backend_metadata():
    entry = target()
    client, control, runtime = provider(entry)
    result = await generate(client, entry)
    assert service.parse_decision(result, {"m1"}).labels.needs_reply is True
    request = runtime.request
    assert request["enableTrace"] is False
    assert request["flowAliasIdentifier"] == entry.alias_id
    document = request["inputs"][0]["content"]["document"]
    assert "alice@example.test" not in document
    assert "2026-10-07T01:23:45+00:00" in document and '"m1"' in document
    assert control.closed and runtime.closed and runtime.stream.closed


async def test_live_stream_without_node_type_uses_verified_output_node():
    entry = target()
    observed = events()
    observed[0]["flowOutputEvent"].pop("nodeType")
    client, _, _ = provider(entry, Stream(observed))
    assert service.parse_decision(await generate(client, entry), {"m1"}).status == "classified"


@pytest.mark.parametrize("stream_events", [
    [], events()[:1], events()[1:], events() + events(),
    [{"flowCompletionEvent": {"completionReason": "INPUT_REQUIRED"}}],
    [{"flowOutputEvent": {"nodeName": "Wrong", "nodeType": "FlowOutputNode",
                          "content": {"document": "{}"}}}],
    [{"flowTraceEvent": {}}],
])
async def test_invalid_or_partial_flow_output_has_no_badges(stream_events):
    entry = target()
    client, control, runtime = provider(entry, Stream(stream_events))
    with pytest.raises(ApiError) as error:
        await generate(client, entry)
    assert error.value.code == "classification_output_invalid"
    assert control.closed and runtime.closed and runtime.stream.closed
    assert client.slots.acquire(blocking=False)
    client.slots.release()


async def test_alias_changed_during_inference_rejected():
    entry = target()
    client, control, runtime = provider(entry)

    def changed():
        yield events()[0]
        control.alias["updatedAt"] = "changed"
        yield events()[1]

    runtime.stream = Stream(changed())
    with pytest.raises(ApiError) as error:
        await generate(client, entry)
    assert error.value.code == "classification_release_changed"


async def test_upstream_event_sanitized_and_single_attempt():
    entry = target()
    client, _, runtime = provider(
        entry, Stream([{"accessDeniedException": {"message": "private"}}])
    )
    with pytest.raises(ApiError) as error:
        await generate(client, entry)
    assert error.value.code == "classification_provider_unavailable"
    assert "private" not in error.value.message
    assert runtime.closed


def test_selected_flow_pins_release_without_converse_model(monkeypatch):
    settings = get_settings()
    for key, value in {"classification_enabled": True, "classification_model_id": "",
                       "classification_transport": "bedrock_flow",
                       "classification_flow_manifest": target().model_dump_json(),
                       "bedrock_mail_processing_acknowledged": True,
                       "gmail_source_mode": "on_demand"}.items():
        monkeypatch.setattr(settings, key, value)
    first = service.release()
    assert first.flow == target() and first.model == target().model_profile_arn
    monkeypatch.setattr(settings, "classification_flow_manifest",
                        target().model_copy(update={"version": "2"}).model_dump_json())
    assert service.release().identifier != first.identifier
    monkeypatch.setattr(settings, "classification_flow_manifest", "{}")
    with pytest.raises(ApiError, match="published classification Flow"):
        service.release()


def test_cancellation_before_start_does_not_invoke():
    entry = target()
    client, _, runtime = provider(entry)
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(ApiError):
        client._call("", {}, model=entry.model_profile_arn, region=entry.region,
                     timeout=90, stopped=stopped, flow=entry)
    assert runtime.request is None


def test_published_prompt_content_is_verified():
    entry = target()
    control = Control(entry)
    control.get_prompt = lambda **kw: {
        "arn": entry.prompt_arn, "defaultVariant": "classify",
        "variants": [flows.prompt_variant(
            f"arn:aws:bedrock:{flows.REGION}::foundation-model/amazon.nova-micro-v1:0")],
    }
    with pytest.raises(FlowError, match="workflow_release_changed"):
        flows.verify_target(control, entry)
