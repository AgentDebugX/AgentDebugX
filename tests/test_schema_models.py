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
