"""Ensure frontend exports and replay assets remain tied to runtime contracts."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

from app.classification import evaluate, service
from app.classification.contracts import (
    Action,
    Category,
    ClassificationRequest,
    ClassificationResponse,
    Priority,
)
from app.classification.flows import HAIKU

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "ml/evals/classification/v1.json"


def test_exported_schemas_and_frontend_fixtures():
    folder = ROOT / "docs/classification"
    for name, model in (("request", ClassificationRequest), ("response", ClassificationResponse)):
        assert json.loads((folder / f"{name}.schema.json").read_text()) == model.model_json_schema()
    fixtures = json.loads((folder / "frontend-fixtures.json").read_text())
    assert fixtures["fixture_only"] is True
    for value in fixtures["responses"].values():
        ClassificationResponse.model_validate_json(json.dumps(value))


def test_prompt_manifest_and_native_bert_label_contracts():
    folder = ROOT / "ml/evals/classification"
    release = json.loads((folder / "release.json").read_text())
    assert release["prompt_sha256"] == hashlib.sha256(service.PROMPT.encode()).hexdigest()
    assert release["output_schema"] == service.SCHEMA
    assert release["policy_version"] == service.POLICY_VERSION
    assert release["selected_candidate"] == "haiku"
    assert release["model_id"] == "au." + HAIKU
    assert release["candidates"] == ["au." + HAIKU]
    models = json.loads((folder / "bert-labels.json").read_text())["models"]
    for name, label_type in (("category", Category), ("priority", Priority), ("action", Action)):
        assert set(get_args(label_type)) == set(models[name]["label2id"])


async def test_fixture_validation_and_replay_scoring(tmp_path):
    args = SimpleNamespace(fixtures=CASES, predictions=None, live=False)
    report = await evaluate.run(args)
    assert report["mode"] == "fixture_validation" and report["cases"] == 12
    assert report["model_quality_verified"] is False
    cases = json.loads(CASES.read_text())["cases"]
    predictions = {case["id"]: case["expected"] for case in cases}
    predictions[cases[0]["id"]]["labels"]["needs_reply"] = "false"
    args.predictions = tmp_path / "outputs.json"
    args.predictions.write_text(json.dumps(predictions))
    report = await evaluate.run(args)
    assert report["mode"] == "replay"
    assert report["invalid_outputs_or_provider_failures"] == 1
    assert report["joint_correct"] == 11
    assert report["model_quality_verified"] is False
