"""Replay real AWS shapes through the bounded adapter; no live model calls."""

import asyncio
import copy
import json
import threading
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.session import Session
from botocore.validate import validate_parameters
from pydantic import ValidationError
from sqlalchemy import select

from app.api.errors import ApiError
from app.assistant import routing, tasks
from app.assistant.summary import digest
from app.assistant.worker import run_once
from app.config import get_settings
from app.db.models import ArtifactRevision, AssistantTask
from app.workflows import registry
from app.workflows.bedrock_flows import FlowError, FlowInvoker
from tests.conftest import needs_pg
from tests.test_draft_workflows import OUTPUT, artifact_for, draft_task
from tests.test_durable_tasks import GENERATED, FakeModel, create_task, mailbox

__all__ = ["mailbox"]
ACCOUNT = "123456789012"
PROFILE = f"arn:aws:bedrock:ap-southeast-2:{ACCOUNT}:inference-profile/{registry.PROFILE}"
FLOW = f"arn:aws:bedrock:ap-southeast-2:{ACCOUNT}:flow/ABCDEFGHIJ"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/ThreadlyFlow"
TARGET = {
    "implementation": "bedrock_flow",
    "region": "ap-southeast-2",
    "flow_arn": FLOW,
    "alias_id": "KLMNOPQRST",
    "version": "1",
    "model_profile_arn": PROFILE,
    "execution_role_arn": ROLE,
    "definition_hash": digest(registry.flow_definition(PROFILE)),
}


def manifest(target=None):
    return {
        "schema_version": "1.0",
        "operations": {
            op: copy.deepcopy(TARGET if target is None else target) for op in registry.OPERATIONS
        },
    }


@pytest.fixture
def configure(monkeypatch):
    def apply(value=None):
        monkeypatch.setenv("INFERENCE_PROVIDER", "bedrock")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "synthetic-router")
        monkeypatch.setenv(
            "ASSISTANT_WORKFLOW_MANIFEST", json.dumps(manifest() if value is None else value)
        )
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


def output(text=None):
    return {
        "flowOutputEvent": {
            "nodeName": "Output",
            "nodeType": "FlowOutputNode",
            "content": {"document": json.dumps(GENERATED) if text is None else text},
        }
    }


def complete(reason="SUCCESS"):
    return {"flowCompletionEvent": {"completionReason": reason}}


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event

    def close(self):
        self.closed = True


class FakeSdk:
    def __init__(self, events=None):
        self.stream = Stream([output(), complete()] if events is None else events)
        self.alias_calls = 0
        self.invocations = []
        self.alias_change = False
        self.definition = registry.flow_definition(PROFILE)
        self.closed = 0

    def get_flow_alias(self, **kwargs):
        self.alias_calls += 1
        return {
            "arn": FLOW + "/alias/KLMNOPQRST",
            "updatedAt": datetime(2026, 9, 15, tzinfo=UTC),
            "routingConfiguration": [{"flowVersion": "2" if self.alias_change else "1"}],
            "ResponseMetadata": {"RequestId": str(self.alias_calls)},
        }

    def get_flow_version(self, **kwargs):
        return {
            "arn": FLOW,
            "version": "1",
            "status": "Prepared",
            "executionRoleArn": ROLE,
            "definition": self.definition,
        }

    def invoke_flow(self, **kwargs):
        validate_parameters(
            kwargs,
            Session()
            .get_service_model("bedrock-agent-runtime")
            .operation_model("InvokeFlow")
            .input_shape,
        )
        self.invocations.append(kwargs)
        return {"responseStream": self.stream, "executionId": "synthetic-execution"}

    def close(self):
        self.closed += 1

    def invoker(self):
        return FlowInvoker(lambda service, entry: self)


@pytest.mark.parametrize(
    "change",
    [
        {"alias_id": "TSTALIASID"},
        {"version": "DRAFT"},
        {"version": "0"},
        {"flow_arn": FLOW.replace(ACCOUNT, "000000000000")},
        {"region": "us-east-1"},
        {"model_profile_arn": PROFILE.replace("/au.", "/global.")},
        {"definition_hash": "a" * 64},
        {"timeout_seconds": 180},
        {"max_events": 999},
        {"tools": ["send_email"]},
    ],
)
def test_registry_rejects_unreviewed_targets_and_budgets(change):
    with pytest.raises(ValidationError):
        registry.FlowEntry.model_validate({**TARGET, **change})


def test_new_config_does_not_reinterpret_pinned_manifest(configure):
    configure()
    release = registry.release_manifest()
    configure(manifest({"implementation": "native"}))
    assert isinstance(
        registry.pinned_manifest(release).operations["draft_reply"], registry.FlowEntry
    )
    assert isinstance(registry.load_manifest().operations["draft_reply"], registry.NativeEntry)
    edited = {**release, "contract_hash": "a" * 64}
    with pytest.raises(ApiError) as error:
        registry.pinned_manifest(edited)
    assert error.value.code == "release_unavailable"


def test_bad_registry_is_unavailable_without_logging_content(configure, monkeypatch, caplog):
    configure()
    monkeypatch.setenv("ASSISTANT_WORKFLOW_MANIFEST", '{"secret":"DO_NOT_PRINT"}')
    get_settings.cache_clear()
    with pytest.raises(ApiError) as error:
        registry.load_manifest()
    assert "DO_NOT_PRINT" not in str(error.value) + caplog.text


async def test_aws_shape_masking_complete_output_and_client_cleanup():
    sdk = FakeSdk()
    result = await sdk.invoker().invoke(
        registry.FlowEntry.model_validate(TARGET), "Mail private@example.test about the update"
    )
    assert result.text == json.dumps(GENERATED)
    assert result.provenance["flow_version"] == "1"
    assert "private@example.test" not in json.dumps(sdk.invocations)
    assert "<EMAIL_1>" in json.dumps(sdk.invocations)
    assert sdk.stream.closed and sdk.closed == 2
    assert sdk.alias_calls == 2  # Different request metadata is not alias movement.


@pytest.mark.parametrize(
    "events,code,retryable",
    [
        ([], "incomplete_flow_output", True),
        ([output()], "incomplete_flow_output", True),
        ([complete()], "invalid_flow_output", False),
        ([output(), output(), complete()], "invalid_flow_output", False),
        ([output(), complete(), complete()], "invalid_flow_output", False),
        ([output(), complete(), output()], "invalid_flow_output", False),
        ([output(), complete("FAILED")], "invalid_flow_output", False),
        (
            [output(), {"accessDeniedException": {"message": "SECRET"}}],
            "workflow_upstream_rejected",
            False,
        ),
        (
            [output(), {"throttlingException": {"message": "SECRET"}}],
            "workflow_upstream_unavailable",
            True,
        ),
        (
            [{"flowMultiTurnInputRequestEvent": {"content": {"document": "SECRET"}}}],
            "workflow_input_required",
            False,
        ),
        ([complete("INPUT_REQUIRED")], "workflow_input_required", False),
        ([{"flowTraceEvent": {"trace": "SECRET"}}], "invalid_flow_output", False),
        ([output({"tool": "send_email"}), complete()], "invalid_flow_output", False),
        ([output("x" * 30001), complete()], "invalid_flow_output", False),
        (
            [
                output(),
                ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": "SECRET"}}, "InvokeFlow"
                ),
            ],
            "workflow_upstream_rejected",
            False,
        ),
    ],
)
async def test_incomplete_adversarial_and_error_streams_never_publish(
    events, code, retryable, caplog
):
    sdk = FakeSdk(events)
    with pytest.raises(FlowError) as error:
        await sdk.invoker().invoke(registry.FlowEntry.model_validate(TARGET), "synthetic")
    assert error.value.code == code and error.value.retryable is retryable
    assert "SECRET" not in str(error.value) + caplog.text
    assert sdk.stream.closed and sdk.closed == 2


async def test_drift_and_extra_write_node_block_before_invocation():
    for change in ("alias", "node"):
        sdk = FakeSdk()
        if change == "alias":
            sdk.alias_change = True
        else:
            sdk.definition["nodes"].append({"name": "Send", "type": "LambdaFunction"})
        with pytest.raises(FlowError, match="workflow_release_changed"):
            await sdk.invoker().invoke(registry.FlowEntry.model_validate(TARGET), "synthetic")
        assert sdk.invocations == []


async def test_alias_changed_during_inference_discards_result():
    sdk = FakeSdk()
    original = sdk.invoke_flow

    def invoke(**kwargs):
        response = original(**kwargs)
        sdk.alias_change = True
        return response

    sdk.invoke_flow = invoke
    with pytest.raises(FlowError, match="workflow_release_changed"):
        await sdk.invoker().invoke(registry.FlowEntry.model_validate(TARGET), "synthetic")


async def test_cancellation_before_invocation_stops_background_dispatch():
    entered, release = threading.Event(), threading.Event()
    sdk = FakeSdk()
    original = sdk.get_flow_version

    def get_version(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    sdk.get_flow_version = get_version
    running = asyncio.create_task(
        sdk.invoker().invoke(registry.FlowEntry.model_validate(TARGET), "synthetic")
    )
    assert await asyncio.to_thread(entered.wait, 5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    # Await an explicit background completion signal; do not depend on sleeps.
    closed = threading.Event()
    sdk.close = lambda: closed.set()
    release.set()
    assert await asyncio.to_thread(closed.wait, 5)
    assert not sdk.invocations


@needs_pg
async def test_summary_route_worker_flow_postgres_artifact_and_owner_checks(
    configure,
    db_sessionmaker,
    mailbox,
    db_client,
    auth_headers,
):
    configure()
    tid, _ = await create_task(db_sessionmaker, mailbox[0])
    sdk = FakeSdk()
    model = FakeModel(error=AssertionError("Explicit summary must not call native inference"))
    await run_once(db_sessionmaker, model, sdk.invoker())
    result = db_client.get(f"/assistant/tasks/{tid}", headers=auth_headers(mailbox[0])).json()
    assert result["state"] == "succeeded"
    artifact = await artifact_for(db_sessionmaker, tid)
    assert artifact.payload["kind"] == "summary" and artifact.provenance["flow_version"] == "1"
    assert (
        db_client.get(
            f"/assistant/artifacts/{artifact.id}", headers=auth_headers(mailbox[1])
        ).status_code
        == 404
    )
    assert len(sdk.invocations) == 1 and not model.calls


@needs_pg
@pytest.mark.parametrize("reply", [False, True])
async def test_flow_drafts_preserve_exact_backend_envelope(
    configure, db_sessionmaker, mailbox, reply
):
    configure()
    tid, _ = await draft_task(db_sessionmaker, mailbox[0], reply=reply)
    generated = {**OUTPUT, "subject": "Re: Release", "sources": [1]} if reply else OUTPUT
    sdk = FakeSdk([output(json.dumps(generated)), complete()])
    await run_once(
        db_sessionmaker, FakeModel(error=AssertionError("No native call")), sdk.invoker()
    )
    artifact = await artifact_for(db_sessionmaker, tid)
    assert artifact.payload["kind"] == "draft"
    assert artifact.draft_envelope["bcc"] == ["private@example.test"]
    assert "private@example.test" not in json.dumps(sdk.invocations)
    assert "recipient@example.test" not in json.dumps(sdk.invocations)


@needs_pg
async def test_config_rollback_keeps_queued_flow_target(configure, db_sessionmaker, mailbox):
    configure()
    tid, _ = await create_task(db_sessionmaker, mailbox[0])
    configure(manifest({"implementation": "native"}))
    sdk = FakeSdk()
    await run_once(
        db_sessionmaker, FakeModel(error=AssertionError("Do not reinterpret queue")), sdk.invoker()
    )
    assert (await artifact_for(db_sessionmaker, tid)).provenance["provider"] == "bedrock_flow"


@needs_pg
async def test_flow_generated_unknown_evidence_is_rejected(configure, db_sessionmaker, mailbox):
    configure()
    tid, _ = await create_task(db_sessionmaker, mailbox[0])
    bad = {**GENERATED, "decisions": [{"text": "Invented", "sources": [999]}]}
    await run_once(
        db_sessionmaker, FakeModel(), FakeSdk([output(json.dumps(bad)), complete()]).invoker()
    )
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, tid)
        assert task.state == "failed" and task.error_code == "invalid_summary_output"
        assert (await session.execute(select(ArtifactRevision))).scalars().all() == []


@needs_pg
async def test_native_queue_survives_flow_activation(configure, db_sessionmaker, mailbox):
    configure(manifest({"implementation": "native"}))
    async with db_sessionmaker.begin() as session:
        tid, _ = await create_task(db_sessionmaker, mailbox[0])
        task = await session.get(AssistantTask, tid)
        task.release = routing.release_manifest()
    configure()
    sdk = FakeSdk()
    await run_once(db_sessionmaker, FakeModel(), sdk.invoker())
    assert not sdk.invocations
    assert (await artifact_for(db_sessionmaker, tid)).provenance["provider"] == "fake"


def test_release_bundle_has_only_expected_graphs_and_narrow_worker_permissions():
    from app.workflows.release import assemble, caller_policy, template

    bundle = template(PROFILE)
    assert len(bundle["Resources"]) == 4
    assert {r["Type"] for r in bundle["Resources"].values()} == {
        "AWS::Bedrock::Flow",
        "AWS::IAM::Role",
    }
    policy = caller_policy(assemble(manifest()["operations"]))
    assert policy["Statement"][0]["Resource"] == [FLOW + "/alias/KLMNOPQRST"]
    assert policy["Statement"][0]["Action"] == ["bedrock:InvokeFlow"]
    assert "*" not in json.dumps(policy)


@needs_pg
async def test_flow_cancellation_during_generation_fences_artifact(
    configure,
    db_sessionmaker,
    mailbox,
):
    configure()
    tid, _ = await create_task(db_sessionmaker, mailbox[0])
    entered, release = asyncio.Event(), asyncio.Event()
    sdk = FakeSdk()

    class BlockingInvoker:
        async def invoke(self, entry, prompt):
            entered.set()
            await release.wait()
            return await sdk.invoker().invoke(entry, prompt)

    worker = asyncio.create_task(run_once(db_sessionmaker, FakeModel(), BlockingInvoker()))
    await asyncio.wait_for(entered.wait(), 3)
    async with db_sessionmaker.begin() as session:
        task = await session.get(AssistantTask, tid)
        await tasks.cancel(session, mailbox[0], tid, task.version)
    release.set()
    await worker
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, tid)).state == "cancelled"
        assert (await session.execute(select(ArtifactRevision))).scalars().all() == []


def test_workflow_configuration_api_requires_auth_and_never_claims_remote_health(
    configure,
    client,
    auth_headers,
):
    configure()
    assert client.get("/assistant/workflows").status_code == 401
    response = client.get("/assistant/workflows", headers=auth_headers(1))
    assert response.status_code == 200
    assert not response.json()["remote_resources_verified"]
    assert "flow_arn" not in response.text
    assert response.json()["operations"]["draft_reply"]["external_actions"] is False


async def test_replay_harness_uses_runtime_schemas_and_records_no_false_live_result():
    from app.workflows.bedrock_flows import FlowResult
    from app.workflows.evaluate import evaluate

    class Replay:
        def __init__(self):
            self.calls = 0

        async def invoke(self, entry, prompt):
            self.calls += 1
            generated = [GENERATED, {**OUTPUT, "subject": "Re: Project"}, OUTPUT][self.calls - 1]
            return FlowResult(json.dumps(generated), {"provider": "fake"})

    replay = Replay()
    report = await evaluate(registry.Manifest.model_validate(manifest()), replay)
    assert report["passed"] == report["total"] == replay.calls == 3
    assert not report["production_approved"]
    assert report["language_quality_review"] == "pending"


async def test_sdk_read_timeout_is_retryable_and_sanitized():
    sdk = FakeSdk([output(), ReadTimeoutError(endpoint_url="https://SECRET.invalid")])
    with pytest.raises(FlowError) as error:
        await sdk.invoker().invoke(registry.FlowEntry.model_validate(TARGET), "synthetic")
    assert error.value.retryable and error.value.code == "workflow_upstream_unavailable"
    assert "SECRET" not in str(error.value)
    assert sdk.stream.closed
