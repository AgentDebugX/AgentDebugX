from __future__ import annotations

import json

import pytest

from agentdebug.ingest import ConversionError, convert_file, convert_payload
from agentdebug.ingest.adapters.importers import detect_payload_format
from agentdebug.schema import AgentTrajectory, EventType


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'messages': [{'role': 'user', 'content': 'hello'}]}, 'messages'),
        ({'conversations': [{'from': 'human', 'value': 'hello'}]}, 'conversations'),
        ({'events': [{'event_type': 'plan', 'output': 'hello'}]}, 'event_list'),
        ({'spans': [{'span_id': 'span-1', 'type': 'tool'}]}, 'openai_agents_spans'),
        ([{'role': 'user', 'content': 'hello'}], 'message_list'),
        ([{'url': '/search', 'content': 'page'}], 'webshop_pages'),
    ],
)
def test_detect_payload_format(payload, expected: str) -> None:
    assert detect_payload_format(payload) == expected


@pytest.mark.parametrize(
    ('format_name', 'payload', 'expected_framework'),
    [
        ('messages', {'messages': [{'role': 'user', 'content': 'goal'}]}, 'messages'),
        ('conversations', {'conversations': [{'from': 'human', 'value': 'goal'}]}, 'conversation_rollout'),
        ('event_list', {'events': [{'type': 'tool.result', 'error': 'boom'}]}, 'event_list'),
        ('openai_agents_spans', {'spans': [{'span_id': 's1', 'type': 'tool', 'error': 'boom'}]}, 'openai-agents'),
        ('crewai_events', {'events': [{'event': 'tool_usage_error', 'error': 'boom'}]}, 'crewai'),
        ('langgraph_callbacks', {'events': [{'event': 'on_chain_start', 'run_id': 'r1', 'name': 'planner'}]}, 'langgraph'),
        ('webshop_pages', [{'url': '/item', 'content': 'item page'}], 'webshop'),
    ],
)
def test_convert_common_formats(
    format_name: str,
    payload,
    expected_framework: str,
) -> None:
    trajectory = convert_payload(payload, format=format_name, trace_id='trace-format')

    assert trajectory.trace_id == 'trace-format'
    assert trajectory.framework == expected_framework
    assert trajectory.events
    assert all(event.trace_id == trajectory.trace_id for event in trajectory.events)


def test_convert_claude_code_tool_pair() -> None:
    payload = [
        {
            'type': 'assistant',
            'sessionId': 'session-1',
            'uuid': 'a1',
            'message': {
                'role': 'assistant',
                'content': [
                    {'type': 'tool_use', 'id': 'call-1', 'name': 'search', 'input': {'q': 'x'}},
                ],
            },
        },
        {
            'type': 'user',
            'sessionId': 'session-1',
            'uuid': 'u1',
            'parentUuid': 'a1',
            'message': {
                'role': 'user',
                'content': [
                    {'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'done'},
                ],
            },
        },
    ]

    trajectory = convert_payload(payload, format='claude_code')

    assert [event.event_type for event in trajectory.events] == [
        EventType.TOOL_CALL.value,
        EventType.TOOL_RESULT.value,
    ]
    assert trajectory.events[1].parent_event_id == trajectory.events[0].event_id


def test_convert_hermes_tool_error() -> None:
    payload = {
        'id': 'hermes-1',
        'source': 'cli',
        'started_at': 1,
        'message_count': 2,
        'messages': [
            {
                'role': 'assistant',
                'tool_calls': [{'id': 'c1', 'function': {'name': 'search', 'arguments': '{"q":"x"}'}}],
            },
            {'role': 'tool', 'tool_call_id': 'c1', 'content': 'Error: unavailable'},
        ],
    }

    trajectory = convert_payload(payload, format='hermes')

    assert trajectory.trace_id == 'hermes-1'
    assert trajectory.events[-1].agent_name == 'search'
    assert trajectory.events[-1].error == 'Error: unavailable'


def test_convert_gaia_odr_minimal_payload() -> None:
    trajectory = convert_payload(
        {'task_id': 'gaia-1', 'task': {'Question': 'What?'}, 'status': 'ok'},
        format='gaia_odr',
    )

    assert trajectory.trace_id == 'gaia-1'
    assert trajectory.events[0].event_type == EventType.RUN_START.value
    assert trajectory.events[-1].event_type == EventType.RUN_END.value


def test_convert_file_and_missing_file(tmp_path) -> None:
    path = tmp_path / 'messages.json'
    path.write_text(json.dumps({'messages': [{'role': 'user', 'content': 'goal'}]}), encoding='utf-8')

    trajectory = convert_file(path, format='auto')

    assert isinstance(trajectory, AgentTrajectory)
    with pytest.raises(ConversionError, match='does not exist'):
        convert_file(tmp_path / 'missing.json')


@pytest.mark.parametrize('payload', [[1], 'text', 42])
def test_invalid_payloads_raise_conversion_error(payload) -> None:
    with pytest.raises(ConversionError):
        convert_payload(payload, format='auto')


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ConversionError, match='unsupported format'):
        convert_payload({}, format='unknown')
