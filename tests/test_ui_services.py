from __future__ import annotations

from agentdebug.inspect.ui.services import (
    _to_dict,
    _extract_chat_content,
    _extract_json_payload,
    _extract_partial_continuation_payload,
    _normalize_chat_endpoint,
    _normalize_generated_events,
)
from agentdebug.schema import DiagnosticReport, FailureFinding, FailureMode


def test_ui_report_serialization_uses_confidence_policy() -> None:
    finding = FailureFinding(
        failure_mode=FailureMode(
            mode_id='test',
            name='Test',
            family='test',
            description='Test finding.',
        ),
        confidence=0.9,
    )
    report = DiagnosticReport(
        trace_id='trace',
        findings=[finding],
        metadata={'analyzer': 'HeuristicAnalyzer'},
    )

    assert 'confidence' not in _to_dict(report)['findings'][0]


def test_normalize_chat_endpoint_variants() -> None:
    assert _normalize_chat_endpoint('https://host/v1') == 'https://host/v1/chat/completions'
    assert _normalize_chat_endpoint('https://host/v1/') == 'https://host/v1/chat/completions'
    assert _normalize_chat_endpoint('https://host') == 'https://host/v1/chat/completions'
    assert _normalize_chat_endpoint('https://host/chat/completions') == 'https://host/chat/completions'


def test_extract_chat_content_supports_text_blocks() -> None:
    payload = {
        'choices': [
            {'message': {'content': [{'text': 'first'}, {'text': 'second'}]}}
        ]
    }

    assert _extract_chat_content(payload) == 'first\nsecond'
    assert _extract_chat_content({}) == ''


def test_extract_json_payload_supports_fenced_json() -> None:
    assert _extract_json_payload('```json\n{"ok": true}\n```') == {'ok': True}
    assert _extract_json_payload('not-json') is None


def test_extract_partial_continuation_recovers_complete_events() -> None:
    text = (
        '{"continuation_events": ['
        '{"event_type":"plan","output":"one"},'
        '{"event_type":"tool.call","input":{"q":"x"}},'
        '{"event_type":'
    )

    payload = _extract_partial_continuation_payload(text)

    assert payload is not None
    assert payload['_partial'] is True
    assert len(payload['continuation_events']) == 2


def test_normalize_generated_events_assigns_parent_and_steps() -> None:
    events = _normalize_generated_events(
        {
            'continuation_events': [
                {'event_type': 'plan', 'output': 'retry'},
                {'event_type': 'tool.result', 'output': 'done'},
            ]
        },
        parent_event_id='evt-parent',
        generated_trace_id='trace-generated',
        checkpoint_step_index=4,
    )

    assert [event['step_index'] for event in events] == [4, 5]
    assert events[0]['parent_event_id'] == 'evt-parent'
    assert events[1]['parent_event_id'] == events[0]['event_id']
    assert all(event['trace_id'] == 'trace-generated' for event in events)
