from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    EventType,
    model_to_json,
    model_to_dict,
    new_id,
    report_from_json,
    trajectory_from_json,
)


def test_trajectory_json_round_trip_preserves_nested_data(
    failed_trajectory: AgentTrajectory,
) -> None:
    payload = model_to_json(failed_trajectory, indent=2)
    restored = trajectory_from_json(payload)

    assert restored == failed_trajectory
    assert restored.events[1].metadata == {'attempt': 1}
    assert json.loads(payload)['events'][0]['event_type'] == 'plan'


def test_report_json_round_trip_preserves_findings(
    diagnostic_report: DiagnosticReport,
) -> None:
    restored = report_from_json(model_to_json(diagnostic_report))

    assert restored == diagnostic_report
    assert restored.findings[0].failure_mode.mode_id == 'test.missing_constraint'


@pytest.mark.parametrize('analyzer', ['HeuristicAnalyzer', 'DeepDebugAnalyzer'])
def test_non_llm_report_output_omits_confidence_recursively(
    diagnostic_report: DiagnosticReport,
    analyzer: str,
) -> None:
    diagnostic_report.metadata.update(
        {'analyzer': analyzer, 'confidence': 0.8}
    )
    diagnostic_report.attribution = {
        'primary': {'confidence': 0.7},
        'hypotheses': [{'confidence': 0.6}],
    }
    diagnostic_report.recovery = {
        'primary': {'confidence': 0.5},
        'proposals': [{'confidence': 0.4}],
    }

    payload = model_to_dict(diagnostic_report)

    assert 'confidence' not in json.dumps(payload)
    assert 'confidence' not in model_to_json(diagnostic_report)
    assert diagnostic_report.findings[0].confidence == 0.8


def test_llm_judge_report_output_preserves_confidence(
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.metadata['analyzer'] = 'LLMJudgeAnalyzer'
    diagnostic_report.attribution = {'primary': {'confidence': 0.7}}

    payload = model_to_dict(diagnostic_report)

    assert payload['findings'][0]['confidence'] == 0.8
    assert payload['attribution']['primary']['confidence'] == 0.7


def test_prefix_returns_independent_event_list(
    failed_trajectory: AgentTrajectory,
) -> None:
    prefix = failed_trajectory.prefix(1)
    prefix.events.clear()

    assert len(failed_trajectory.events) == 2
    assert failed_trajectory.prefix(-1).events == []


def test_model_default_collections_are_not_shared() -> None:
    first = AgentTrajectory(trace_id='first')
    second = AgentTrajectory(trace_id='second')
    first.metadata['owner'] = 'first'
    first.add_event(AgentEvent(trace_id='first'))

    assert second.metadata == {}
    assert second.events == []


def test_invalid_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentEvent(trace_id='trace', event_type='not-an-event')


def test_generated_ids_are_prefixed_and_unique() -> None:
    first = new_id('trace')
    second = new_id('trace')

    assert first.startswith('trace_')
    assert second.startswith('trace_')
    assert first != second
