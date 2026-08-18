from __future__ import annotations

import json

import pytest

from agentdebug.inspect.discussion import (
    DiscussionService,
    InvalidCitationError,
    ToolBoundsError,
    UnknownEventError,
)
from agentdebug.runtime.llm import CompletionResult, TokenUsage
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    DiagnosticReport,
    EventType,
    Modality,
)


def _snapshots() -> tuple[AgentTrajectory, DiagnosticReport]:
    trajectory = AgentTrajectory(
        trace_id='trace-discuss',
        goal='Summarize a plain text trace.',
        events=[
            AgentEvent(
                event_id='evt-a',
                trace_id='trace-discuss',
                event_type=EventType.AGENT_STEP,
                step_index=3,
                input='first text input',
                output='first text output',
                artifacts=[
                    Artifact(
                        uri='C:/private/screens/step.png',
                        modality=Modality.IMAGE,
                        media_type='image/png',
                        description='safe description',
                    )
                ],
            ),
            AgentEvent(
                event_id='evt-b',
                trace_id='trace-discuss',
                event_type=EventType.ERROR,
                step_index=3,
                error='second event has the same step index',
            ),
        ],
    )
    report = DiagnosticReport(
        report_id='report-discuss',
        trace_id='trace-discuss',
        root_cause_event_id='evt-b',
        root_cause_step_index=3,
        summary='Original report summary.',
    )
    return trajectory, report


def test_compact_text_context_and_event_id_identity() -> None:
    trajectory, report = _snapshots()
    service = DiscussionService(trajectory, report)

    context = service.build_context()

    assert context['trace']['events'][0]['input'] == 'first text input'
    assert service.get_event_details('evt-a')['output'] == 'first text output'
    assert 'uri' not in service.get_event_details('evt-a')['artifacts'][0]
    assert service.get_event_details('evt-b')['error'].startswith('second event')
    assert [item['event_id'] for item in service.get_event_range(0, 1)] == [
        'evt-a',
        'evt-b',
    ]


def test_report_and_trajectory_are_pinned_snapshots() -> None:
    trajectory, report = _snapshots()
    service = DiscussionService(trajectory, report)
    original_digest = service.digest

    trajectory.events[0].output = 'mutated'
    report.summary = 'mutated'
    detached = service.report_snapshot
    detached.summary = 'also mutated'

    assert service.get_event_details('evt-a')['output'] == 'first text output'
    assert service.get_report_details()['summary'] == 'Original report summary.'
    assert service.digest == original_digest


def test_tool_bounds_unknown_events_and_citations() -> None:
    trajectory, report = _snapshots()
    service = DiscussionService(trajectory, report)

    with pytest.raises(UnknownEventError):
        service.get_event_details('missing')
    with pytest.raises(ToolBoundsError):
        service.get_event_range(-1, 0)
    with pytest.raises(ToolBoundsError):
        service.get_event_range(0, 2)
    with pytest.raises(InvalidCitationError):
        service.validate_citations(['missing'])

    assert [citation.event_id for citation in service.validate_citations(
        ['evt-a', {'event_id': 'evt-a'}, {'event_id': 'evt-b', 'quote': 'error'}]
    )] == ['evt-a', 'evt-b']


def test_structured_revision_draft_is_parsed_without_mutating_report() -> None:
    trajectory, report = _snapshots()
    service = DiscussionService(trajectory, report)
    response = {
        'answer': 'The second event is better evidence.',
        'citations': [{'event_id': 'evt-b'}],
        'report_revision': {
            'base_report_id': 'report-discuss',
            'changes': {
                'summary': 'Revised summary.',
                'root_cause_event_id': 'evt-b',
            },
            'rationale': 'The event has an explicit error.',
        },
    }

    result = service.parse_response(response)

    assert result.revision_draft is not None
    assert result.revision_draft.changes['summary'] == 'Revised summary.'
    assert result.revision_draft.base_report_digest == service.report_digest
    assert service.get_report_details()['summary'] == 'Original report summary.'


def test_fake_llm_callable_and_plain_text_response() -> None:
    trajectory, report = _snapshots()
    captured = {}

    def fake_llm(messages, tools):
        captured['messages'] = messages
        captured['tools'] = tools
        return CompletionResult(
            text=json.dumps({
                'content': 'Evidence is in evt-a.',
                'citations': ['evt-a'],
            }),
            raw={'must_not_escape': True},
            usage=TokenUsage(prompt_tokens=10, completion_tokens=4, calls=1),
        )

    result = DiscussionService(trajectory, report, fake_llm).discuss('Why?')

    assert result.content == 'Evidence is in evt-a.'
    assert result.usage == {
        'prompt_tokens': 10,
        'completion_tokens': 4,
        'total_tokens': 14,
        'calls': 1,
        'cost_usd': 0.0,
    }
    assert [tool['function']['name'] for tool in captured['tools']] == [
        'get_event_details',
        'get_event_range',
        'get_report_details',
    ]
    assert 'must_not_escape' not in result.to_dict()
