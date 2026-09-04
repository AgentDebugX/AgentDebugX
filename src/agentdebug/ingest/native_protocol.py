"""Consistency checks for stored native tool-calling transcripts.

A trajectory recorded from a provider's native tool-calling API carries two
descriptions of every tool turn: the wire envelope (the OpenAI-shaped
``messages`` list, with ``tool_calls`` on assistant messages and
``tool_call_id`` on tool messages) and the executed events (``agent.step`` /
``error`` for the turn, ``tool.call`` / ``tool.result`` for what actually
ran). The two are written by different code paths and drift silently: a
multi-call assistant turn executed as one call, a tool message whose id no
assistant message ever issued, a ``tool.call`` whose name or arguments are not
what the envelope said. None of that is caught by schema validation, because
every field is individually well-formed.

This module answers "do the two descriptions agree?" for one trajectory, as
a list of violation records rather than a boolean, so a corpus audit can
count by reason and a strict ingest can refuse with a specific message.

Conventions read from the trajectory (all optional; a trajectory that carries
none of them is simply "not a native transcript" and yields
:data:`NO_MESSAGES`):

* ``trajectory.metadata['messages']`` -- the verbatim wire transcript.
* ``event.metadata['prompt_n_messages']`` on ``agent.step`` / ``error``
  events -- the index in ``messages`` of the assistant message that turn
  produced (equivalently, how many messages were in the prompt).
* ``event.metadata['tool_call_id']`` on ``tool.call`` events -- the provider
  id of the call that ran.
* ``event.input == {'tool': name, 'args': {...}}`` on ``tool.call`` and
  ``event.input == {'tool': name}`` on ``tool.result``.
* ``error`` starting with ``multiple_tool_calls:`` on an ``error`` event --
  the harness rejected a multi-call turn without executing any of it.
* ``leaked_tool_name`` on the step (in ``metadata`` or a dict ``output``) --
  the provider corrupted the tool name in transit (for example by welding a
  channel token onto it) and the harness executed the repaired name.
* ``envelope == 'text_protocol_fallback'`` on the step -- the model answered
  in a text action protocol instead of a native call, so the executed
  ``tool.call`` legitimately has no provider id.

:func:`native_tool_message_violations` checks the wire transcript alone,
for importers that have the messages but no executed events yet.
:func:`native_tool_protocol_violations` checks the full trajectory.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

Violation = Dict[str, Any]
"""One finding: always ``trace_uid`` and ``reason``; usually ``message_index``."""

ArgumentParser = Callable[[str], Any]
"""Parses the ``function.arguments`` string of a tool call into a value."""

# --------------------------------------------------------------------------- #
# Reason vocabulary. Stable strings: corpus audits count by them.
# --------------------------------------------------------------------------- #

NO_MESSAGES = 'native trace has no messages list'
TOOL_CALLS_NOT_LIST = 'assistant tool_calls must be a non-empty list'
CALL_ID_MISSING = 'assistant tool call has no non-empty id'
CALL_IDS_NOT_UNIQUE = 'assistant tool call ids are not unique'
CALLS_NOT_ANSWERED_ONCE = (
    'assistant tool calls are not answered exactly once before next turn'
)
NO_UNIQUE_TURN_POINTER = 'native assistant turn has no unique step/error event pointer'
MULTI_CALL_NOT_REJECTED = (
    'multiple native calls were not rejected as one non-executed error turn'
)
REJECTED_CALL_EXECUTED = 'rejected native call has execution events'
NO_UNIQUE_EXECUTION_PAIR = 'accepted native call has no unique call/result event pair'
CALL_ID_DISAGREES = 'tool.call event id disagrees with assistant envelope'
FUNCTION_NAME_MISSING = 'assistant tool call has no non-empty function name'
CALL_NAME_DISAGREES = 'tool.call event name disagrees with assistant envelope'
ARGS_NOT_OBJECT = 'accepted assistant tool arguments are not a JSON object'
CALL_ARGS_DISAGREE = 'tool.call event args disagree with assistant envelope'
RESULT_NAME_DISAGREES = 'tool.result event name disagrees with tool.call event'
ORPHAN_TOOL_MESSAGE = (
    'tool message has no immediately preceding assistant tool-call batch'
)
CALL_ID_WITHOUT_ENVELOPE = 'tool.call event id has no assistant envelope'
CALL_WITHOUT_ID_OR_FALLBACK = (
    'tool.call event has no provider id or declared text fallback'
)

#: Every reason this module can emit, in the order the checks run.
REASONS = (
    NO_MESSAGES,
    TOOL_CALLS_NOT_LIST,
    CALL_ID_MISSING,
    CALL_IDS_NOT_UNIQUE,
    CALLS_NOT_ANSWERED_ONCE,
    NO_UNIQUE_TURN_POINTER,
    MULTI_CALL_NOT_REJECTED,
    REJECTED_CALL_EXECUTED,
    NO_UNIQUE_EXECUTION_PAIR,
    CALL_ID_DISAGREES,
    FUNCTION_NAME_MISSING,
    CALL_NAME_DISAGREES,
    ARGS_NOT_OBJECT,
    CALL_ARGS_DISAGREE,
    RESULT_NAME_DISAGREES,
    ORPHAN_TOOL_MESSAGE,
    CALL_ID_WITHOUT_ENVELOPE,
    CALL_WITHOUT_ID_OR_FALLBACK,
)

#: Reasons decidable from the wire transcript alone (no executed events needed).
MESSAGE_REASONS = (
    TOOL_CALLS_NOT_LIST,
    CALL_ID_MISSING,
    CALL_IDS_NOT_UNIQUE,
    CALLS_NOT_ANSWERED_ONCE,
    ORPHAN_TOOL_MESSAGE,
)

#: The ``error`` prefix a harness writes when it refuses a multi-call turn.
MULTIPLE_TOOL_CALLS_PREFIX = 'multiple_tool_calls:'
#: The step annotation that declares a text-protocol action without a provider id.
TEXT_PROTOCOL_FALLBACK = 'text_protocol_fallback'

_TURN_EVENT_TYPES = frozenset({EventType.AGENT_STEP.value, EventType.ERROR.value})
_EXECUTION_EVENT_TYPES = frozenset(
    {EventType.TOOL_CALL.value, EventType.TOOL_RESULT.value}
)


def parse_json_arguments(raw: str) -> Any:
    """Default ``function.arguments`` parser: strict JSON, ``None`` when invalid.

    Harnesses that repair provider output before executing it (raw newlines
    inside a JSON string, a stray closing bracket) should pass the same
    repair as ``parse_arguments`` so the check compares what the harness
    actually saw; otherwise a repaired turn reads as :data:`ARGS_NOT_OBJECT`.
    """
    try:
        return json.loads(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Wire transcript
# --------------------------------------------------------------------------- #


@dataclass
class _AssistantTurn:
    """One assistant message carrying ``tool_calls``, after the wire checks."""

    index: int
    calls: List[Any]
    call_ids: List[str]
    violations: List[Violation] = field(default_factory=list)
    #: False when a wire check already rejected the turn outright; the
    #: event-level checks do not run on it.
    checkable: bool = True


@dataclass
class _WireScan:
    turns: List[_AssistantTurn]
    claimed_tool_indexes: Set[int]
    envelope_call_ids: Set[str]
    orphans: List[Violation]


def _scan_wire(messages: Sequence[Any], trace_uid: str) -> _WireScan:
    turns: List[_AssistantTurn] = []
    claimed: Set[int] = set()
    envelope_call_ids: Set[str] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            continue
        calls = message.get('tool_calls')
        if calls is None:
            continue
        turn = _AssistantTurn(index=index, calls=[], call_ids=[])
        turns.append(turn)
        if not isinstance(calls, list) or not calls:
            turn.violations.append(
                _violation(trace_uid, TOOL_CALLS_NOT_LIST, message_index=index)
            )
            turn.checkable = False
            continue
        turn.calls = calls

        tool_ids: List[Any] = []
        for following_index in range(index + 1, len(messages)):
            following = messages[following_index]
            if not isinstance(following, dict) or following.get('role') != 'tool':
                break
            claimed.add(following_index)
            tool_ids.append(following.get('tool_call_id'))

        raw_ids = [call.get('id') if isinstance(call, dict) else None for call in calls]
        if any(not isinstance(call_id, str) or not call_id for call_id in raw_ids):
            turn.violations.append(
                _violation(trace_uid, CALL_ID_MISSING, message_index=index)
            )
            turn.checkable = False
            continue
        call_ids: List[str] = [str(call_id) for call_id in raw_ids]
        turn.call_ids = call_ids
        envelope_call_ids.update(call_ids)
        if len(set(call_ids)) != len(call_ids):
            turn.violations.append(
                _violation(
                    trace_uid, CALL_IDS_NOT_UNIQUE, message_index=index, call_ids=call_ids
                )
            )
        if Counter(tool_ids) != Counter(call_ids):
            turn.violations.append(
                _violation(
                    trace_uid,
                    CALLS_NOT_ANSWERED_ONCE,
                    message_index=index,
                    call_ids=call_ids,
                    tool_result_ids=tool_ids,
                )
            )

    orphans: List[Violation] = []
    for index, message in enumerate(messages):
        if (
            isinstance(message, dict)
            and message.get('role') == 'tool'
            and index not in claimed
        ):
            orphans.append(
                _violation(
                    trace_uid,
                    ORPHAN_TOOL_MESSAGE,
                    message_index=index,
                    tool_call_id=message.get('tool_call_id'),
                )
            )
    return _WireScan(
        turns=turns,
        claimed_tool_indexes=claimed,
        envelope_call_ids=envelope_call_ids,
        orphans=orphans,
    )


def native_tool_message_violations(
    messages: Sequence[Any], *, trace_uid: Optional[str] = None
) -> List[Violation]:
    """Check an OpenAI-shaped ``messages`` list on its own.

    Every assistant ``tool_calls`` id must be non-empty and unique within its
    turn, and must be answered by exactly one ``tool`` message before the
    next non-tool message; every ``tool`` message must sit in such a batch.
    This is the subset of :func:`native_tool_protocol_violations` a strict
    provider would itself reject, and it needs no executed events, so an
    importer can run it on the raw export. Reasons: :data:`MESSAGE_REASONS`.
    """
    scan = _scan_wire(messages, trace_uid or '')
    violations: List[Violation] = []
    for turn in scan.turns:
        violations.extend(turn.violations)
    violations.extend(scan.orphans)
    return violations


# --------------------------------------------------------------------------- #
# Full trajectory
# --------------------------------------------------------------------------- #


def native_tool_protocol_violations(
    trajectory: AgentTrajectory,
    *,
    trace_uid: Optional[str] = None,
    parse_arguments: Optional[ArgumentParser] = None,
) -> List[Violation]:
    """Find native turns whose stored wire envelope disagrees with the executed turn.

    Runs the wire checks of :func:`native_tool_message_violations`, then for
    each assistant tool-call turn requires exactly one ``agent.step`` or
    ``error`` event pointing at it (``prompt_n_messages``); a multi-call turn
    must be that ``error`` event with a ``multiple_tool_calls:`` error and no
    execution; a single accepted call must have exactly one ``tool.call`` and
    one ``tool.result`` at the same ``step_index`` agreeing with the envelope
    on id, name and arguments; and every ``tool.call`` event must carry an id
    the envelope issued or belong to a step that declared a text fallback.

    ``trace_uid`` labels every record (defaults to ``trajectory.trace_id``).
    ``parse_arguments`` replaces :func:`parse_json_arguments` when the harness
    repaired the provider's argument string before executing it.
    """
    uid = trace_uid if trace_uid is not None else trajectory.trace_id
    parse = parse_arguments or parse_json_arguments
    messages = trajectory.metadata.get('messages')
    if not isinstance(messages, list):
        return [_violation(uid, NO_MESSAGES)]

    turn_events: Dict[int, List[AgentEvent]] = defaultdict(list)
    execution_events: Dict[int, List[AgentEvent]] = defaultdict(list)
    for event in trajectory.events:
        kind = _event_type(event)
        if kind in _TURN_EVENT_TYPES:
            pointer = event.metadata.get('prompt_n_messages')
            if _is_int(pointer):
                turn_events[pointer].append(event)
        elif kind in _EXECUTION_EVENT_TYPES and _is_int(event.step_index):
            execution_events[event.step_index].append(event)

    scan = _scan_wire(messages, uid)
    violations: List[Violation] = []
    for turn in scan.turns:
        violations.extend(turn.violations)
        if not turn.checkable:
            continue
        events = turn_events.get(turn.index, [])
        if len(events) != 1:
            violations.append(
                _violation(
                    uid,
                    NO_UNIQUE_TURN_POINTER,
                    message_index=turn.index,
                    matching_events=len(events),
                )
            )
            continue
        event = events[0]
        if len(turn.calls) > 1:
            if not _is_rejected_multi_call(event):
                violations.append(
                    _violation(
                        uid,
                        MULTI_CALL_NOT_REJECTED,
                        message_index=turn.index,
                        call_ids=turn.call_ids,
                        event_type=_event_type(event),
                        event_error=event.error,
                    )
                )
                continue
            # A rejected multi-call turn must not have run anything: a call or
            # result event at its step means the harness executed a partial turn
            # after telling the model it had refused it.
            step_index = event.step_index
            ran = execution_events.get(step_index, []) if _is_int(step_index) else []
            if ran:
                violations.append(
                    _violation(
                        uid,
                        REJECTED_CALL_EXECUTED,
                        message_index=turn.index,
                        tool_call_events=sum(
                            1 for item in ran
                            if _event_type(item) == EventType.TOOL_CALL.value
                        ),
                        tool_result_events=sum(
                            1 for item in ran
                            if _event_type(item) == EventType.TOOL_RESULT.value
                        ),
                    )
                )
            continue
        violations.extend(
            _check_single_call(uid, turn, event, execution_events, parse)
        )

    violations.extend(scan.orphans)
    violations.extend(
        _check_tool_call_events(uid, trajectory, scan.envelope_call_ids)
    )
    return violations


def _check_single_call(
    trace_uid: str,
    turn: _AssistantTurn,
    event: AgentEvent,
    execution_events: Dict[int, List[AgentEvent]],
    parse: ArgumentParser,
) -> List[Violation]:
    index = turn.index
    step_index = event.step_index
    step_execution = execution_events.get(step_index, []) if _is_int(step_index) else []
    call_events = [
        item for item in step_execution if _event_type(item) == EventType.TOOL_CALL.value
    ]
    result_events = [
        item
        for item in step_execution
        if _event_type(item) == EventType.TOOL_RESULT.value
    ]
    if _event_type(event) == EventType.ERROR.value:
        if call_events or result_events:
            return [
                _violation(
                    trace_uid,
                    REJECTED_CALL_EXECUTED,
                    message_index=index,
                    tool_call_events=len(call_events),
                    tool_result_events=len(result_events),
                )
            ]
        return []
    if len(call_events) != 1 or len(result_events) != 1:
        return [
            _violation(
                trace_uid,
                NO_UNIQUE_EXECUTION_PAIR,
                message_index=index,
                tool_call_events=len(call_events),
                tool_result_events=len(result_events),
            )
        ]

    violations: List[Violation] = []
    call = turn.calls[0]
    function = call.get('function') if isinstance(call, dict) else None
    function = function if isinstance(function, dict) else {}
    expected_name = function.get('name')
    raw_args = function.get('arguments')
    expected_args = parse(raw_args) if isinstance(raw_args, str) else raw_args
    executed = call_events[0]
    executed_input = executed.input if isinstance(executed.input, dict) else {}
    executed_id = executed.metadata.get('tool_call_id')
    executed_name = executed_input.get('tool')
    executed_args = executed_input.get('args')
    leaked_name = _step_annotation(event, 'leaked_tool_name')

    if executed_id != turn.call_ids[0]:
        violations.append(
            _violation(
                trace_uid,
                CALL_ID_DISAGREES,
                message_index=index,
                assistant_tool_call_id=turn.call_ids[0],
                event_tool_call_id=executed_id,
            )
        )
    if not isinstance(expected_name, str) or not expected_name:
        violations.append(
            _violation(trace_uid, FUNCTION_NAME_MISSING, message_index=index)
        )
    elif executed_name != expected_name and leaked_name != expected_name:
        violations.append(
            _violation(
                trace_uid,
                CALL_NAME_DISAGREES,
                message_index=index,
                assistant_tool_name=expected_name,
                event_tool_name=executed_name,
            )
        )
    if not isinstance(expected_args, dict):
        violations.append(_violation(trace_uid, ARGS_NOT_OBJECT, message_index=index))
    elif executed_args != expected_args:
        violations.append(
            _violation(
                trace_uid,
                CALL_ARGS_DISAGREE,
                message_index=index,
                assistant_args=expected_args,
                event_args=executed_args,
            )
        )

    result_input = (
        result_events[0].input if isinstance(result_events[0].input, dict) else {}
    )
    if result_input.get('tool') != executed_name:
        violations.append(
            _violation(
                trace_uid,
                RESULT_NAME_DISAGREES,
                message_index=index,
                tool_call_name=executed_name,
                tool_result_name=result_input.get('tool'),
            )
        )
    return violations


def _check_tool_call_events(
    trace_uid: str, trajectory: AgentTrajectory, envelope_call_ids: set
) -> List[Violation]:
    violations: List[Violation] = []
    for event in trajectory.events:
        if _event_type(event) != EventType.TOOL_CALL.value:
            continue
        call_id = event.metadata.get('tool_call_id')
        if isinstance(call_id, str) and call_id:
            if call_id not in envelope_call_ids:
                violations.append(
                    _violation(
                        trace_uid,
                        CALL_ID_WITHOUT_ENVELOPE,
                        event_tool_call_id=call_id,
                        step_index=event.step_index,
                    )
                )
            continue
        declared_fallback = any(
            _step_annotation(item, 'envelope') == TEXT_PROTOCOL_FALLBACK
            for item in trajectory.events
            if _event_type(item) == EventType.AGENT_STEP.value
            and item.step_index == event.step_index
        )
        if not declared_fallback:
            violations.append(
                _violation(
                    trace_uid,
                    CALL_WITHOUT_ID_OR_FALLBACK,
                    step_index=event.step_index,
                )
            )
    return violations


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _violation(trace_uid: str, reason: str, **detail: Any) -> Violation:
    record: Violation = {'trace_uid': trace_uid}
    message_index = detail.pop('message_index', None)
    if message_index is not None:
        record['message_index'] = message_index
    record['reason'] = reason
    record.update(detail)
    return record


def _event_type(event: AgentEvent) -> str:
    """The event type as its string value, whether stored as enum or str."""
    value = event.event_type
    return value.value if isinstance(value, EventType) else str(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_rejected_multi_call(event: AgentEvent) -> bool:
    return _event_type(event) == EventType.ERROR.value and str(
        event.error or ''
    ).startswith(MULTIPLE_TOOL_CALLS_PREFIX)


def _step_annotation(event: AgentEvent, key: str) -> Any:
    """Read a harness annotation from ``metadata`` or, failing that, a dict ``output``."""
    if key in event.metadata:
        return event.metadata.get(key)
    if isinstance(event.output, dict):
        return event.output.get(key)
    return None


__all__ = [
    'ARGS_NOT_OBJECT',
    'CALLS_NOT_ANSWERED_ONCE',
    'CALL_ARGS_DISAGREE',
    'CALL_IDS_NOT_UNIQUE',
    'CALL_ID_DISAGREES',
    'CALL_ID_MISSING',
    'CALL_ID_WITHOUT_ENVELOPE',
    'CALL_NAME_DISAGREES',
    'CALL_WITHOUT_ID_OR_FALLBACK',
    'FUNCTION_NAME_MISSING',
    'MESSAGE_REASONS',
    'MULTIPLE_TOOL_CALLS_PREFIX',
    'MULTI_CALL_NOT_REJECTED',
    'NO_MESSAGES',
    'NO_UNIQUE_EXECUTION_PAIR',
    'NO_UNIQUE_TURN_POINTER',
    'ORPHAN_TOOL_MESSAGE',
    'REASONS',
    'REJECTED_CALL_EXECUTED',
    'RESULT_NAME_DISAGREES',
    'TEXT_PROTOCOL_FALLBACK',
    'TOOL_CALLS_NOT_LIST',
    'Violation',
    'native_tool_message_violations',
    'native_tool_protocol_violations',
    'parse_json_arguments',
]
