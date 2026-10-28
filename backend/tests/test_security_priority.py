"""Synthetic counterfactual contract checks; no model-quality or live-mail claims."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.classification import evaluate, service
from app.classification.contracts import ClassificationRequest
from tests import test_classification as classification_tests

configured = classification_tests.configured
pipeline = classification_tests.pipeline
decision = classification_tests.decision


def security_decision(priority='Low', source='m1', refs=None):
    output = decision(priority='High')
    output['security_priority'] = {
        'source_id': source, 'priority_if_recognised': priority, 'evidence': refs or ['m1'],
    }
    return output


@pytest.mark.parametrize('baseline', ['Low', 'Medium', 'High'])
async def test_baseline_mapped_with_no_extra_read_or_model_call(pipeline, baseline):
    _, provider, reads = pipeline
    provider.output = security_decision(baseline)
    result = await service.classify(1, 1, 'me@example.test', 'abc123', ClassificationRequest())
    assert result.labels.priority == 'High'
    assert result.security_priority.message_id == 'def456'
    assert result.security_priority.priority_if_recognised == baseline
    assert result.security_priority.evidence == ['def456']
    assert len(reads) == 2 and len(provider.calls) == 1
    assert result.persisted is False


@pytest.mark.parametrize('output', [
    security_decision(source='m9'), security_decision(refs=['m9']),
    security_decision(refs=['m1', 'm1']), security_decision('Urgent'),
    {**security_decision(), 'labels': decision(priority='Low')['labels'],
     'security_priority': security_decision('High')['security_priority']},
    {**security_decision(), 'security_priority': {
        **security_decision()['security_priority'], 'body': 'PRIVATE_CODE'}},
])
def test_unbound_ambiguous_or_extra_baselines_fail_closed(output):
    with pytest.raises(ApiError):
        service.parse_decision(json.dumps(output), {'m1': 'def456'})


async def test_baseline_cannot_follow_new_incoming_or_outgoing_mail(pipeline):
    data, provider, _ = pipeline
    provider.output = security_decision()
    newer = copy.deepcopy(data['messages'][0])
    newer.update(gmail_msg_id='aaa111', received_at='2026-10-06T02:00:00+00:00')
    data['messages'].append(newer)
    result = await service.classify(1, 1, 'me@example.test', 'abc123', ClassificationRequest())
    assert result.latest_incoming.message_id == 'aaa111'
    assert result.security_priority is None
    newer['reply_metadata']['label_ids'] = ['SENT']
    newer['is_from_user'] = True
    result = await service.classify(1, 1, 'me@example.test', 'abc123', ClassificationRequest())
    assert result.latest_incoming is None and result.security_priority is None


async def test_missing_optional_baseline_preserves_released_output(pipeline):
    result = await service.classify(1, 1, 'me@example.test', 'abc123', ClassificationRequest())
    assert result.security_priority is None and result.labels.priority == 'Medium'


async def test_versioned_synthetic_fixtures_and_baseline_scoring(tmp_path):
    fixtures = (Path(__file__).resolve().parents[2]
                / 'ml/evals/classification/security-recognition-v1.json')
    data = json.loads(fixtures.read_text())
    args = SimpleNamespace(fixtures=fixtures, predictions=None, live=False)
    report = await evaluate.run(args)
    assert report['cases'] == 8 and report['model_quality_verified'] is False
    predictions = {c['id']: c['expected'] for c in data['cases']}
    # A structurally valid but wrong downgrade must fail evaluation.
    baseline = predictions['earlier-independent-urgency']['security_priority']
    baseline['priority_if_recognised'] = 'Low'
    args.predictions = tmp_path / 'authored-contracts.json'
    args.predictions.write_text(json.dumps(predictions))
    report = await evaluate.run(args)
    assert report['joint_correct'] == 7
    assert report['security_priority_scores'] == {'correct': 7, 'total': 8}
    assert report['invalid_outputs_or_provider_failures'] == 0
