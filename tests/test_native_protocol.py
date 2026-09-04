"""The native tool-calling wire envelope must agree with the executed events.

Each test builds the smallest trajectory that exhibits one reason from
``agentdebug.ingest.native_protocol.REASONS`` and asserts that exactly that
reason is reported; the first test is the consistent transcript every other
test perturbs, so a regression that reports nothing for everything fails here.
"""
from __future__ import annotations

import json

import pytest

from agentdebug.ingest import ConversionError, convert_payload
from agentdebug.ingest import native_protocol as npm
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

TRACE = 'tr-native'


def _messages(*, call_id: str = 'call_1', name: str = 'lookup', args: str = '{"q": "cd"}',
              extra_calls: int = 0, answer_id: str | None = 'call_1') -> list[dict]:
    calls = [{'id': call_id, 'type': 'function',
              'function': {'name': name, 'arguments': args}}]
    for k in range(extra_calls):
        calls.append({'id': f'{call_id}_x{k}', 'type': 'function',
                      'function': {'name': name, 'arguments': args}})
    msgs: list[dict] = [
        {'role': 'system', 'content': 'You are an agent.'},
        {'role': 'user', 'content': 'Find the cd.'},
        {'role': 'assistant', 'content': None, 'tool_calls': calls},
    ]
    if answer_id is not None:
        msgs.append({'role': 'tool', 'tool_call_id': answer_id, 'content': 'cd 3 on desk 1'})
    return msgs


def _trajectory(messages: list[dict], events: list[AgentEvent] | None = None) -> AgentTrajectory:
    return AgentTrajectory(trace_id=TRACE, metadata={'messages': messages},
                           events=events if events is not None else _executed())


def _executed(*, call_id: str = 'call_1', name: str = 'lookup', args: dict | None = None,
              pointer: int = 2, step: int = 0, result_name: str | None = None) -> list[AgentEvent]:
    args = {'q': 'cd'} if args is None else args
    return [
        AgentEvent(trace_id=TRACE, event_type=EventType.AGENT_STEP, step_index=step,
                   output='calling lookup', metadata={'prompt_n_messages': pointer}),
        AgentEvent(trace_id=TRACE, event_type=EventType.TOOL_CALL, step_index=step,
                   input={'tool': name, 'args': args}, metadata={'tool_call_id': call_id}),
        AgentEvent(trace_id=TRACE, event_type=EventType.TOOL_RESULT, step_index=step,
                   input={'tool': result_name or name}, output='cd 3 on desk 1'),
    ]


def _reasons(trajectory: AgentTrajectory) -> list[str]:
    return [v['reason'] for v in npm.native_tool_protocol_violations(trajectory, trace_uid=TRACE)]


def test_consistent_single_call_turn_has_no_violations() -> None:
    assert _reasons(_trajectory(_messages())) == []


def test_every_reason_is_a_distinct_string() -> None:
    assert len(set(npm.REASONS)) == len(npm.REASONS)
    assert set(npm.MESSAGE_REASONS) <= set(npm.REASONS)


def test_no_messages_is_reported_not_crashed() -> None:
    traj = AgentTrajectory(trace_id=TRACE, events=_executed())
    assert _reasons(traj) == [npm.NO_MESSAGES]


def test_tool_calls_must_be_a_non_empty_list() -> None:
    msgs = _messages()
    msgs[2]['tool_calls'] = []
    assert npm.TOOL_CALLS_NOT_LIST in _reasons(_trajectory(msgs))


def test_call_id_missing_and_duplicate_ids() -> None:
    msgs = _messages(call_id='')
    assert npm.CALL_ID_MISSING in _reasons(_trajectory(msgs, _executed(call_id='')))
    dup = _messages(extra_calls=1)
    dup[2]['tool_calls'][1]['id'] = 'call_1'
    assert npm.CALL_IDS_NOT_UNIQUE in _reasons(_trajectory(dup))


def test_unanswered_call_and_orphan_tool_message() -> None:
    unanswered = _messages(answer_id=None)
    assert npm.CALLS_NOT_ANSWERED_ONCE in _reasons(_trajectory(unanswered))
    orphan = _messages()
    orphan.append({'role': 'user', 'content': 'and then?'})
    orphan.append({'role': 'tool', 'tool_call_id': 'call_never_issued', 'content': 'x'})
    assert npm.ORPHAN_TOOL_MESSAGE in _reasons(_trajectory(orphan))


def test_stale_envelope_replayed_without_a_turn_pointer() -> None:
    """The bug this module was written for: a second assistant envelope no event produced."""
    msgs = _messages()
    msgs.append({'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call_2', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}]})
    msgs.append({'role': 'tool', 'tool_call_id': 'call_2', 'content': 'again'})
    assert npm.NO_UNIQUE_TURN_POINTER in _reasons(_trajectory(msgs))


def test_multi_call_turn_must_be_rejected_not_executed() -> None:
    msgs = _messages(extra_calls=1, answer_id=None)
    msgs.append({'role': 'tool', 'tool_call_id': 'call_1', 'content': 'a'})
    msgs.append({'role': 'tool', 'tool_call_id': 'call_1_x0', 'content': 'b'})
    executed_anyway = _executed()
    assert npm.MULTI_CALL_NOT_REJECTED in _reasons(_trajectory(msgs, executed_anyway))
    rejected = [AgentEvent(trace_id=TRACE, event_type=EventType.ERROR, step_index=0,
                           error='multiple_tool_calls: expected exactly one tool call, got 2',
                           metadata={'prompt_n_messages': 2})]
    assert _reasons(_trajectory(msgs, rejected)) == []
    rejected_but_ran = rejected + _executed()[1:]
    assert npm.REJECTED_CALL_EXECUTED in _reasons(_trajectory(msgs, rejected_but_ran))


def test_accepted_call_needs_one_call_and_one_result_event() -> None:
    only_step = _executed()[:1]
    assert npm.NO_UNIQUE_EXECUTION_PAIR in _reasons(_trajectory(_messages(), only_step))


def test_envelope_and_event_must_agree_on_id_name_and_args() -> None:
    assert npm.CALL_ID_DISAGREES in _reasons(_trajectory(_messages(), _executed(call_id='call_9')))
    assert npm.CALL_NAME_DISAGREES in _reasons(_trajectory(_messages(), _executed(name='other')))
    assert npm.CALL_ARGS_DISAGREE in _reasons(_trajectory(_messages(), _executed(args={'q': 'dvd'})))
    assert npm.RESULT_NAME_DISAGREES in _reasons(
        _trajectory(_messages(), _executed(result_name='other')))


def test_function_name_missing_and_arguments_not_an_object() -> None:
    assert npm.FUNCTION_NAME_MISSING in _reasons(_trajectory(_messages(name=''), _executed(name='')))
    assert npm.ARGS_NOT_OBJECT in _reasons(_trajectory(_messages(args='[1, 2]')))


def test_tool_call_event_without_envelope_or_text_fallback() -> None:
    events = _executed()
    stray = AgentEvent(trace_id=TRACE, event_type=EventType.TOOL_CALL, step_index=1,
                       input={'tool': 'lookup', 'args': {}}, metadata={'tool_call_id': 'call_ghost'})
    assert npm.CALL_ID_WITHOUT_ENVELOPE in _reasons(_trajectory(_messages(), events + [stray]))
    no_id = AgentEvent(trace_id=TRACE, event_type=EventType.TOOL_CALL, step_index=1,
                       input={'tool': 'lookup', 'args': {}})
    assert npm.CALL_WITHOUT_ID_OR_FALLBACK in _reasons(_trajectory(_messages(), events + [no_id]))


def test_message_level_checks_run_on_the_raw_export() -> None:
    reasons = [v['reason'] for v in npm.native_tool_message_violations(_messages(answer_id=None))]
    assert reasons == [npm.CALLS_NOT_ANSWERED_ONCE]
    assert npm.native_tool_message_violations(_messages()) == []


def test_parse_json_arguments_is_strict() -> None:
    assert npm.parse_json_arguments('{"a": 1}') == {'a': 1}
    assert npm.parse_json_arguments('{a: 1}') is None
    assert npm.parse_json_arguments('') is None


def test_violation_records_are_json_and_carry_the_trace_uid() -> None:
    [record] = npm.native_tool_protocol_violations(
        AgentTrajectory(trace_id='other', events=[]), trace_uid=TRACE)
    json.dumps(record)
    assert record['trace_uid'] == TRACE and record['reason'] == npm.NO_MESSAGES


def test_convert_payload_strict_native_refuses_an_invalid_export() -> None:
    payload = {'messages': _messages(answer_id=None)}
    convert_payload(payload, format='messages')  # lenient default still converts
    with pytest.raises(ConversionError, match=npm.CALLS_NOT_ANSWERED_ONCE[:20]):
        convert_payload(payload, format='messages', strict_native=True)
    assert convert_payload({'messages': _messages()}, format='messages', strict_native=True)
