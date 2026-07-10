"""Offline trajectory import adapters.

Runtime adapters in this package observe a live framework and record
``AgentEvent`` objects as the agent runs. The helpers in this module do the
other half of adapter work: they import already-written logs or exported trace
files and normalize them into ``AgentTrajectory``.

The importer intentionally accepts a small set of common shapes rather than a
single dataset schema: OpenAI-style messages, rollout conversations, raw event
lists, WebShop page logs, OpenAI Agents span dumps, CrewAI events, and
LangGraph/LangChain callback logs. Framework-specific fields are preserved in
``event.metadata`` so downstream analyzers can still inspect the native data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union, cast

from agentdebug.core.models import (
    AgentEvent,
    AgentTrajectory,
    EventType,
    model_to_json,
    new_id,
    trajectory_from_json,
)

FormatName = Literal[
    'auto',
    'agenttrajectory',
    'messages',
    'message_list',
    'conversations',
    'event_list',
    'webshop_pages',
    'openai_agents_spans',
    'crewai_events',
    'langgraph_callbacks',
    'openclaw',
    'claude_code',
    'hermes',
    'osworld',
    'gaia_odr',
]


class ConversionError(ValueError):
    """Raised when an offline payload cannot be normalized."""


def detect_payload_format(payload: Any) -> FormatName:
    """Best-effort format detection for common offline agent trace exports."""

    if isinstance(payload, dict) and 'trace_id' in payload and 'events' in payload:
        return 'agenttrajectory'

    if isinstance(payload, dict):
        if _looks_hermes_export(payload):
            return 'hermes'
        if isinstance(payload.get('messages'), list):
            return 'messages'
        if isinstance(payload.get('conversations'), list):
            return 'conversations'
        if isinstance(payload.get('events'), list):
            events = payload['events']
            if _looks_crewai_event_list(events):
                return 'crewai_events'
            if events and all(_looks_langgraph_callback(e) for e in events if isinstance(e, dict)):
                return 'langgraph_callbacks'
            return 'event_list'
        if isinstance(payload.get('spans'), list):
            return 'openai_agents_spans'
        if _looks_webshop_page(payload):
            return 'webshop_pages'

    if isinstance(payload, list):
        if not payload:
            return 'message_list'
        dict_items = [item for item in payload if isinstance(item, dict)]
        if len(dict_items) != len(payload):
            raise ConversionError('list payload must contain JSON objects')
        if all(_looks_webshop_page(item) for item in dict_items):
            return 'webshop_pages'
        if _looks_crewai_event_list(dict_items):
            return 'crewai_events'
        if all(_looks_langgraph_callback(item) for item in dict_items):
            return 'langgraph_callbacks'
        if len(dict_items) == 1 and _looks_hermes_export(dict_items[0]):
            return 'hermes'
        if _looks_claude_code_records(dict_items):
            return 'claude_code'
        if _looks_openclaw_records(dict_items):
            return 'openclaw'
        if all('role' in item or 'content' in item for item in dict_items):
            return 'message_list'
        if all('event_type' in item or 'type' in item for item in dict_items):
            return 'event_list'
        if all('span_id' in item or 'trace_id' in item or 'name' in item for item in dict_items):
            return 'openai_agents_spans'

    raise ConversionError('could not detect offline trajectory format')


def convert_payload(
    payload: Any,
    *,
    format: FormatName = 'auto',
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Convert an offline trace payload into ``AgentTrajectory``.

    ``format='auto'`` is intended for CLI use and common exported logs. Pass an
    explicit format when a payload is ambiguous, for example a list of objects
    that could be either messages or events.
    """

    fmt = detect_payload_format(payload) if format == 'auto' else _normalize_format_name(format)
    if fmt == 'agenttrajectory':
        if not isinstance(payload, (dict, str)):
            raise ConversionError('agenttrajectory payload must be a JSON object or string')
        text = payload if isinstance(payload, str) else json.dumps(payload)
        traj = trajectory_from_json(text)
        if trace_id is not None:
            traj.trace_id = trace_id
            for event in traj.events:
                event.trace_id = trace_id
        if task_id is not None:
            traj.task_id = task_id
        if goal is not None:
            traj.goal = goal
        if framework is not None:
            traj.framework = framework
        return traj
    if fmt in {'messages', 'message_list'}:
        messages = payload.get('messages') if isinstance(payload, dict) else payload
        return _convert_messages_payload(
            payload if isinstance(payload, dict) else {'messages': messages},
            cast(Sequence[Dict[str, Any]], messages),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'conversations':
        if not isinstance(payload, dict):
            raise ConversionError('conversations format expects a JSON object')
        return _convert_conversation_payload(
            payload,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'event_list':
        events_payload = payload.get('events') if isinstance(payload, dict) else payload
        return _convert_event_list(
            payload if isinstance(payload, dict) else {},
            cast(Sequence[Dict[str, Any]], events_payload),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'webshop_pages':
        pages = payload if isinstance(payload, list) else [payload]
        return _convert_webshop_pages(
            cast(Sequence[Dict[str, Any]], pages),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'openai_agents_spans':
        spans = payload.get('spans') if isinstance(payload, dict) else payload
        return _convert_openai_agents_spans(
            payload if isinstance(payload, dict) else {},
            cast(Sequence[Dict[str, Any]], spans),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'crewai_events':
        events = payload.get('events') if isinstance(payload, dict) else payload
        return _convert_crewai_events(
            payload if isinstance(payload, dict) else {},
            cast(Sequence[Dict[str, Any]], events),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'langgraph_callbacks':
        callbacks = payload.get('events') if isinstance(payload, dict) else payload
        return _convert_langgraph_callbacks(
            payload if isinstance(payload, dict) else {},
            cast(Sequence[Dict[str, Any]], callbacks),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'openclaw':
        records = payload if isinstance(payload, list) else [payload]
        return _convert_openclaw_records(
            cast(Sequence[Dict[str, Any]], records),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'claude_code':
        records = payload if isinstance(payload, list) else [payload]
        return _convert_claude_code_records(
            cast(Sequence[Dict[str, Any]], records),
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'hermes':
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ConversionError(
                'hermes format expects a JSON object or single-record JSONL export'
            )
        return _convert_hermes_export(
            payload,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    if fmt == 'gaia_odr':
        if not isinstance(payload, dict):
            raise ConversionError('gaia_odr format expects a JSON object')
        from agentdebug.ingest.adapters import gaia_odr

        return gaia_odr.convert_gaia_odr_payload(
            payload,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    raise ConversionError(f'unsupported offline trajectory format: {fmt}')


def convert_file(
    path: Union[str, Path],
    *,
    format: FormatName = 'auto',
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Load a JSON or JSONL file and normalize it into ``AgentTrajectory``."""

    in_path = Path(path)
    if not in_path.exists():
        raise ConversionError(f'input file does not exist: {in_path}')
    if format != 'auto' and _normalize_format_name(format) == 'gaia_odr':
        from agentdebug.ingest.adapters import gaia_odr

        return gaia_odr.convert_gaia_odr_file(
            in_path,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    payload = _load_jsonl(in_path) if in_path.suffix.lower() == '.jsonl' else json.loads(
        in_path.read_text(encoding='utf-8')
    )
    return convert_payload(
        payload,
        format=format,
        trace_id=trace_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
    )


def convert_directory(
    path: Union[str, Path],
    *,
    format: FormatName = 'auto',
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Convert a trajectory *directory* into ``AgentTrajectory``.

    Existing formats convert an in-memory payload or a single file; OSWorld
    input is a directory (``traj.jsonl``/``trajectory.jsonl`` + screenshots +
    ``result.txt``), so directory handling is a distinct dispatch branch. With
    ``format='auto'`` an OSWorld directory is detected by the presence of a
    ``traj.jsonl`` or ``trajectory.jsonl`` marker.
    """

    in_path = Path(path)
    if not in_path.is_dir():
        raise ConversionError(f'input is not a directory: {in_path}')
    fmt = 'auto' if format == 'auto' else _normalize_format_name(format)
    is_osworld = (in_path / 'traj.jsonl').exists() or (in_path / 'trajectory.jsonl').exists()
    if fmt == 'osworld' or (fmt == 'auto' and is_osworld):
        from agentdebug.ingest.adapters import osworld

        return osworld.convert_osworld_dir(
            in_path,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )
    raise ConversionError(f'directory did not match a known layout: {in_path}')


def write_converted_trajectory(
    trajectory: AgentTrajectory, path: Union[str, Path]
) -> Path:
    """Write a converted trajectory as pretty JSON and return its path."""

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(model_to_json(trajectory, indent=2) + '\n', encoding='utf-8')
    return out


def _normalize_format_name(fmt: str) -> FormatName:
    normalized = fmt.replace('-', '_').lower()
    allowed = {
        'auto',
        'agenttrajectory',
        'messages',
        'message_list',
        'conversations',
        'event_list',
        'webshop_pages',
        'openai_agents_spans',
        'crewai_events',
        'langgraph_callbacks',
        'openclaw',
        'claude_code',
        'hermes',
        'osworld',
        'gaia_odr',
    }
    if normalized not in allowed:
        raise ConversionError(f'unsupported format: {fmt}')
    return cast(FormatName, normalized)


def _load_jsonl(path: Path) -> List[Any]:
    rows: List[Any] = []
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


_HERMES_NATIVE_SESSION_KEYS = {
    'source',
    'user_id',
    'session_key',
    'chat_id',
    'chat_type',
    'thread_id',
    'model',
    'model_config',
    'system_prompt',
    'parent_session_id',
    'started_at',
    'ended_at',
    'end_reason',
    'message_count',
    'tool_call_count',
    'input_tokens',
    'output_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
    'reasoning_tokens',
    'cwd',
    'git_branch',
    'git_repo_root',
    'billing_provider',
    'billing_base_url',
    'billing_mode',
    'estimated_cost_usd',
    'actual_cost_usd',
    'cost_status',
    'cost_source',
    'pricing_version',
    'title',
    'api_call_count',
    'handoff_state',
    'handoff_platform',
    'handoff_error',
    'archived',
}


def _looks_hermes_export(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload.get('messages'), list):
        return False
    if isinstance(payload.get('hermes_session'), dict):
        return True

    evidence = sum(1 for key in _HERMES_NATIVE_SESSION_KEYS if key in payload)
    system_prompt = _opt_str(payload.get('system_prompt')) or ''
    if 'Hermes Agent' in system_prompt or 'hermes-agent' in system_prompt:
        return True

    # SessionDB.export_session() carries database session columns next to
    # messages. Do not require source == "cli"; source is a free text column
    # used by multiple Hermes frontends.
    if payload.get('source') is not None and 'started_at' in payload and 'message_count' in payload:
        return True
    return evidence >= 3 and any(
        key in payload for key in ('source', 'started_at', 'model_config', 'tool_call_count')
    )


def _base_trajectory(
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
    fallback_framework: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTrajectory:
    return AgentTrajectory(
        trace_id=trace_id or new_id('trace'),
        task_id=task_id,
        goal=goal,
        framework=framework or fallback_framework,
        metadata=metadata or {},
    )


def _convert_messages_payload(
    payload: Dict[str, Any],
    messages: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    if not isinstance(messages, Sequence):
        raise ConversionError('messages must be a list')
    resolved_goal = goal or _extract_goal(payload) or _goal_from_messages(messages)
    traj = _base_trajectory(
        trace_id=trace_id,
        task_id=task_id or _opt_str(payload.get('task_id') or payload.get('id')),
        goal=resolved_goal,
        framework=framework,
        fallback_framework=_opt_str(payload.get('framework')) or 'messages',
        metadata={'source_format': 'messages'},
    )
    for idx, message in enumerate(messages):
        role = _opt_str(message.get('role')) or _opt_str(message.get('speaker')) or 'agent'
        content = _message_content(message.get('content'))
        event_type = _event_type_for_role(role)
        traj.add_event(
            AgentEvent(
                trace_id=traj.trace_id,
                agent_name=role,
                event_type=event_type,
                module=_module_for_event_type(event_type),
                step_index=idx,
                input=message.get('input'),
                output=content,
                error=_opt_str(message.get('error')),
                metadata={'source_format': 'messages', 'source_index': idx},
            )
        )
    return traj


def _convert_conversation_payload(
    payload: Dict[str, Any],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    conversations = payload.get('conversations')
    if not isinstance(conversations, Sequence):
        raise ConversionError('conversations must be a list')
    resolved_task_id = task_id or _opt_str(payload.get('item_id') or payload.get('task_id') or payload.get('id'))
    resolved_trace_id = trace_id or (f'conv_{resolved_task_id}' if resolved_task_id else None)
    resolved_goal = goal or _extract_goal(payload) or _goal_from_messages(cast(Sequence[Dict[str, Any]], conversations))
    traj = _base_trajectory(
        trace_id=resolved_trace_id,
        task_id=resolved_task_id,
        goal=resolved_goal,
        framework=framework,
        fallback_framework=_opt_str(payload.get('framework')) or 'conversation_rollout',
        metadata={
            'source_format': 'conversations',
            'reward': payload.get('reward'),
            'success': payload.get('success'),
            'done': payload.get('done'),
        },
    )
    step = 0
    system_prompts: List[str] = []
    for idx, turn in enumerate(conversations):
        if not isinstance(turn, dict):
            continue
        role = _opt_str(turn.get('role') or turn.get('from') or turn.get('speaker')) or 'agent'
        content = _message_content(turn.get('content') or turn.get('value') or turn.get('text'))
        if role.lower() == 'system':
            if content.strip():
                system_prompts.append(content.strip())
            continue
        thought, action, remainder = _split_thought_action(content)
        if thought:
            traj.add_event(
                AgentEvent(
                    trace_id=traj.trace_id,
                    agent_name=role,
                    event_type=EventType.AGENT_STEP,
                    module='reasoning',
                    step_index=step,
                    output=thought,
                    metadata={'source_format': 'conversations', 'source_index': idx},
                )
            )
            step += 1
        if action:
            event_type = _classify_action_event_type(action)
            traj.add_event(
                AgentEvent(
                    trace_id=traj.trace_id,
                    agent_name=role,
                    event_type=event_type,
                    module=_module_for_event_type(event_type),
                    step_index=step,
                    input=action if event_type == EventType.TOOL_CALL else None,
                    output=None if event_type == EventType.TOOL_CALL else action,
                    metadata={'source_format': 'conversations', 'source_index': idx},
                )
            )
            step += 1
            continue
        if remainder:
            event_type = _classify_observation_event(remainder)
            err = 'Nothing happens' if 'nothing happens' in remainder.lower() else None
            traj.add_event(
                AgentEvent(
                    trace_id=traj.trace_id,
                    agent_name=role,
                    event_type=event_type,
                    module=_module_for_event_type(event_type),
                    step_index=step,
                    output=remainder,
                    error=err,
                    metadata={'source_format': 'conversations', 'source_index': idx},
                )
            )
            step += 1
    if system_prompts:
        traj.metadata['system_prompt'] = '\n\n'.join(system_prompts)
    return traj


def _convert_event_list(
    payload: Dict[str, Any],
    events: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    resolved_trace_id = trace_id or _opt_str(payload.get('trace_id')) or new_id('trace')
    traj = _base_trajectory(
        trace_id=resolved_trace_id,
        task_id=task_id or _opt_str(payload.get('task_id')),
        goal=goal or _extract_goal(payload),
        framework=framework,
        fallback_framework=_opt_str(payload.get('framework')) or 'event_list',
        metadata={'source_format': 'event_list'},
    )
    for idx, item in enumerate(events):
        if not isinstance(item, dict):
            continue
        event_type = _coerce_event_type(item.get('event_type') or item.get('type'))
        native = dict(item)
        step_index = _opt_int(
            item.get('step_index') if 'step_index' in item else item.get('step')
        )
        traj.add_event(
            AgentEvent(
                event_id=_opt_str(item.get('event_id') or item.get('id')) or new_id('evt'),
                trace_id=resolved_trace_id,
                parent_event_id=_opt_str(item.get('parent_event_id') or item.get('parent_id')),
                agent_name=_opt_str(item.get('agent_name') or item.get('agent') or item.get('role')) or 'agent',
                event_type=event_type,
                module=_opt_str(item.get('module')) or _module_for_event_type(event_type),
                step_index=step_index if step_index is not None else idx,
                input=item.get('input'),
                output=item.get('output') if 'output' in item else item.get('content'),
                error=_opt_str(item.get('error')),
                duration_ms=item.get('duration_ms'),
                metadata={'source_format': 'event_list', 'native': native},
            )
        )
    return traj


def _convert_webshop_pages(
    pages: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    first = pages[0] if pages else {}
    resolved_task_id = task_id or _task_id_from_webshop(first)
    resolved_trace_id = trace_id or (f'webshop_{resolved_task_id}' if resolved_task_id else None)
    traj = _base_trajectory(
        trace_id=resolved_trace_id,
        task_id=resolved_task_id,
        goal=goal or _opt_str(first.get('goal') or first.get('instruction')),
        framework=framework,
        fallback_framework='webshop',
        metadata={'source_format': 'webshop_pages'},
    )
    for idx, page in enumerate(pages):
        traj.add_event(
            AgentEvent(
                trace_id=traj.trace_id,
                agent_name='environment',
                event_type=EventType.OBSERVATION,
                module='browser',
                step_index=idx,
                input={'url': page.get('url'), 'page': page.get('page')},
                output=page.get('content') or page.get('text') or page.get('observation'),
                metadata={
                    'source_format': 'webshop_pages',
                    'source_index': idx,
                    'native': page,
                },
            )
        )
    return traj


def _convert_openai_agents_spans(
    payload: Dict[str, Any],
    spans: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    resolved_trace_id = trace_id or _opt_str(payload.get('trace_id')) or new_id('trace')
    traj = _base_trajectory(
        trace_id=resolved_trace_id,
        task_id=task_id or _opt_str(payload.get('task_id')),
        goal=goal or _extract_goal(payload),
        framework=framework,
        fallback_framework='openai-agents',
        metadata={'source_format': 'openai_agents_spans'},
    )
    for idx, span in enumerate(spans):
        span_type = _opt_str(span.get('type') or span.get('span_type') or span.get('name')) or ''
        event_type = _event_type_for_span(span_type)
        traj.add_event(
            AgentEvent(
                event_id=_opt_str(span.get('span_id') or span.get('id')) or new_id('evt'),
                trace_id=traj.trace_id,
                parent_event_id=_opt_str(span.get('parent_id') or span.get('parent_span_id')),
                agent_name=_opt_str(span.get('agent') or span.get('agent_name')) or 'agent',
                event_type=event_type,
                module=_module_for_event_type(event_type),
                step_index=idx,
                input=span.get('input') or span.get('arguments'),
                output=span.get('output') or span.get('result'),
                error=_opt_str(span.get('error')),
                duration_ms=span.get('duration_ms'),
                metadata={'source_format': 'openai_agents_spans', 'native': span},
            )
        )
    return traj


def _convert_crewai_events(
    payload: Dict[str, Any],
    events: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    traj = _base_trajectory(
        trace_id=trace_id or _opt_str(payload.get('trace_id')) or new_id('trace'),
        task_id=task_id or _opt_str(payload.get('task_id')),
        goal=goal or _extract_goal(payload),
        framework=framework,
        fallback_framework='crewai',
        metadata={'source_format': 'crewai_events'},
    )
    for idx, event in enumerate(events):
        name = _opt_str(event.get('event') or event.get('type') or event.get('name')) or ''
        event_type = _event_type_for_crewai(name)
        traj.add_event(
            AgentEvent(
                trace_id=traj.trace_id,
                agent_name=_opt_str(event.get('agent') or event.get('agent_name') or event.get('crew')) or 'agent',
                event_type=event_type,
                module=_module_for_event_type(event_type),
                step_index=idx,
                input=event.get('input') or event.get('task'),
                output=event.get('output') or event.get('result'),
                error=_opt_str(event.get('error') or event.get('exception')),
                metadata={'source_format': 'crewai_events', 'native': event},
            )
        )
    return traj


def _convert_langgraph_callbacks(
    payload: Dict[str, Any],
    callbacks: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    traj = _base_trajectory(
        trace_id=trace_id or _opt_str(payload.get('trace_id')) or new_id('trace'),
        task_id=task_id or _opt_str(payload.get('task_id')),
        goal=goal or _extract_goal(payload),
        framework=framework,
        fallback_framework='langgraph',
        metadata={'source_format': 'langgraph_callbacks'},
    )
    for idx, callback in enumerate(callbacks):
        name = _opt_str(callback.get('event') or callback.get('type') or callback.get('name')) or ''
        event_type = _event_type_for_langgraph(name)
        traj.add_event(
            AgentEvent(
                event_id=_opt_str(callback.get('run_id') or callback.get('event_id')) or new_id('evt'),
                trace_id=traj.trace_id,
                parent_event_id=_opt_str(callback.get('parent_run_id') or callback.get('parent_event_id')),
                agent_name=_opt_str(callback.get('name') or callback.get('agent') or callback.get('node')) or 'agent',
                event_type=event_type,
                module=_module_for_event_type(event_type),
                step_index=idx,
                input=callback.get('inputs') or callback.get('input'),
                output=callback.get('outputs') or callback.get('output'),
                error=_opt_str(callback.get('error')),
                metadata={'source_format': 'langgraph_callbacks', 'native': callback},
            )
        )
    return traj


def _convert_openclaw_records(
    records: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    """Convert OpenClaw JSONL streams into an ``AgentTrajectory``.

    OpenClaw stores these logs under
    ``~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl`` and
    ``~/.openclaw/agents/<agentId>/sessions/<sessionId>.trajectory.jsonl``.

    Default/original gate handles the session transcript log
    (``<sessionId>.jsonl``): ``type=session`` plus ``type=message`` records.
    This is the path that existed before these additions; keep it broad
    because older logs use Anthropic-style ``tool_use`` / ``tool_result``
    blocks and Pi-style ``toolUse`` / ``arguments`` blocks.

    Added session variant support covers current transcript logs where
    assistant messages contain ``toolCall`` blocks and tool outputs arrive as
    role-level ``toolResult`` messages.

    Added runtime trajectory gate handles ``<sessionId>.trajectory.jsonl``
    logs where every row has ``traceSchema=openclaw-trajectory`` and typed
    events such as ``prompt.submitted``, ``tool.call``, ``tool.result``, and
    ``model.completed``. Keep it separate from generic event-list conversion
    because OpenClaw stores the important payload under ``data``.

    Tool names become ``event.agent_name`` for ``tool_use`` and ``tool_result``
    blocks so attribution backends can blame the responsible tool rather than
    blaming the assistant for echoing the failure back.
    """
    if _looks_openclaw_trajectory_records(records):
        return _convert_openclaw_trajectory_records(
            records,
            trace_id=trace_id,
            task_id=task_id,
            goal=goal,
            framework=framework,
        )

    traj = _base_trajectory(
        trace_id=trace_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
        fallback_framework='openclaw',
        metadata={'source_format': 'openclaw'},
    )
    # Mutate the trajectory's metadata directly — pydantic copies the dict
    # we passed to _base_trajectory, so a local handle would be a dead alias.
    metadata: Dict[str, Any] = traj.metadata
    default_agent = 'openclaw-agent'
    step_index = 0
    tool_use_index: Dict[str, str] = {}
    tool_event_index: Dict[str, str] = {}
    message_event_index: Dict[str, str] = {}
    first_user_text: Optional[str] = None
    saw_session_header = False

    def _emit(**kwargs: Any) -> AgentEvent:
        nonlocal step_index
        if kwargs.get('timestamp') is None:
            kwargs.pop('timestamp', None)
        event = traj.add_event(AgentEvent(trace_id=traj.trace_id, **kwargs))
        step_index += 1
        return event

    def _emit_for_message(
        message_events: List[AgentEvent], **kwargs: Any
    ) -> AgentEvent:
        event = _emit(**kwargs)
        message_events.append(event)
        return event

    for line_no, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        rtype = record.get('type')

        if rtype == 'session':
            saw_session_header = True
            sid = _opt_str(record.get('id'))
            if sid and trace_id is None:
                traj.trace_id = sid
                for existing in traj.events:
                    existing.trace_id = sid
            ts = _parse_openclaw_ts(record.get('timestamp'))
            if ts is not None:
                traj.started_at = ts
            metadata.update(
                {
                    'openclaw_session_id': sid,
                    'openclaw_session_version': record.get('version'),
                    'openclaw_cwd': record.get('cwd'),
                }
            )
            continue

        if rtype == 'session-meta':
            agent_meta = _opt_str(record.get('agentId'))
            if agent_meta:
                default_agent = agent_meta
                metadata['openclaw_agent_id'] = agent_meta
            continue

        if rtype == 'custom':
            metadata.setdefault('openclaw_custom', []).append(
                {
                    'line': line_no,
                    'customType': record.get('customType'),
                    'data': record.get('data'),
                }
            )
            continue

        if rtype != 'message':
            continue

        message = record.get('message')
        if not isinstance(message, dict):
            continue

        role = _opt_str(message.get('role')) or 'agent'
        blocks = _openclaw_blocks(message.get('content'))
        timestamp = _parse_openclaw_ts(record.get('timestamp') or message.get('timestamp'))
        native_parent_id = _opt_str(record.get('parentId') or record.get('parent_id'))
        parent_event_id = message_event_index.get(native_parent_id or '')
        msg_id = _opt_str(record.get('id'))
        message_events: List[AgentEvent] = []

        if role == 'user' and first_user_text is None:
            for block in blocks:
                if block.get('type') == 'text':
                    text = _openclaw_block_text(block)
                    if text:
                        first_user_text = text.strip()
                        break

        for block in blocks:
            btype = block.get('type')

            if btype == 'text':
                text = _openclaw_block_text(block) or ''
                if not text.strip():
                    continue
                role_l = role.lower()
                if role_l in {'toolresult', 'tool_result'}:
                    tool_use_id = _openclaw_tool_call_id(block, message)
                    tool_name = (
                        tool_use_index.get(tool_use_id or '', '')
                        or _opt_str(message.get('toolName') or message.get('tool_name'))
                        or 'tool'
                    )
                    is_error = bool(message.get('isError') or message.get('is_error'))
                    _emit_for_message(
                        message_events,
                        agent_name=tool_name,
                        event_type=EventType.TOOL_RESULT,
                        module='tool',
                        step_index=step_index,
                        output=None if is_error else text,
                        error=text if is_error else None,
                        timestamp=timestamp,
                        parent_event_id=tool_event_index.get(tool_use_id or '', parent_event_id),
                        metadata={
                            'source_format': 'openclaw',
                            'openclaw_line': line_no,
                            'openclaw_message_id': msg_id,
                            'openclaw_role': role,
                            'openclaw_parent_id': native_parent_id,
                            'openclaw_tool_use_id': tool_use_id,
                            'openclaw_tool_call_id': tool_use_id,
                            'openclaw_is_error': is_error,
                        },
                    )
                    continue
                if role_l == 'user':
                    event_type = EventType.OBSERVATION
                    agent = 'user'
                elif role_l == 'assistant':
                    event_type = EventType.LLM_RESPONSE
                    agent = default_agent
                else:
                    event_type = EventType.OBSERVATION
                    agent = role
                _emit_for_message(
                    message_events,
                    agent_name=agent,
                    event_type=event_type,
                    module='conversation',
                    step_index=step_index,
                    output=text,
                    timestamp=timestamp,
                    parent_event_id=parent_event_id,
                    metadata={
                        'source_format': 'openclaw',
                        'openclaw_line': line_no,
                        'openclaw_message_id': msg_id,
                        'openclaw_role': role,
                        'openclaw_parent_id': native_parent_id,
                    },
                )
                continue

            if btype == 'reasoning':
                _emit_for_message(
                    message_events,
                    agent_name=default_agent,
                    event_type=EventType.REFLECTION,
                    module='reasoning',
                    step_index=step_index,
                    output=_openclaw_block_text(block),
                    timestamp=timestamp,
                    parent_event_id=parent_event_id,
                    metadata={
                        'source_format': 'openclaw',
                        'openclaw_line': line_no,
                        'openclaw_message_id': msg_id,
                        'openclaw_parent_id': native_parent_id,
                        'openclaw_reasoning_id': block.get('id'),
                    },
                )
                continue

            if btype in {'tool_use', 'toolUse', 'toolCall'}:
                tool_name = _opt_str(block.get('name') or block.get('toolName')) or 'tool'
                tool_id = _openclaw_tool_call_id(block, message)
                if tool_id:
                    tool_use_index[tool_id] = tool_name
                tool_input = block.get('input')
                if tool_input is None:
                    tool_input = block.get('arguments')
                event = _emit_for_message(
                    message_events,
                    agent_name=tool_name,
                    event_type=EventType.TOOL_CALL,
                    module='tool',
                    step_index=step_index,
                    input=tool_input,
                    timestamp=timestamp,
                    parent_event_id=parent_event_id,
                    metadata={
                        'source_format': 'openclaw',
                        'openclaw_line': line_no,
                        'openclaw_message_id': msg_id,
                        'openclaw_parent_id': native_parent_id,
                        'openclaw_tool_use_id': tool_id,
                        'openclaw_tool_call_id': tool_id,
                        'openclaw_caller': default_agent,
                    },
                )
                if tool_id:
                    tool_event_index[tool_id] = event.event_id
                continue

            if btype in {'tool_result', 'toolResult'}:
                tool_use_id = _openclaw_tool_call_id(block, message)
                tool_name = (
                    tool_use_index.get(tool_use_id or '', '')
                    or _opt_str(
                        block.get('toolName')
                        or block.get('name')
                        or message.get('toolName')
                        or message.get('tool_name')
                    )
                    or 'tool'
                )
                is_error = bool(
                    block.get('is_error')
                    or block.get('isError')
                    or message.get('is_error')
                    or message.get('isError')
                )
                result_text = _openclaw_block_text(block)
                _emit_for_message(
                    message_events,
                    agent_name=tool_name,
                    event_type=EventType.TOOL_RESULT,
                    module='tool',
                    step_index=step_index,
                    output=None if is_error else result_text,
                    error=result_text if is_error else None,
                    timestamp=timestamp,
                    parent_event_id=tool_event_index.get(tool_use_id or '', parent_event_id),
                    metadata={
                        'source_format': 'openclaw',
                        'openclaw_line': line_no,
                        'openclaw_message_id': msg_id,
                        'openclaw_parent_id': native_parent_id,
                        'openclaw_tool_use_id': tool_use_id,
                        'openclaw_tool_call_id': tool_use_id,
                        'openclaw_is_error': is_error,
                    },
                )
                continue

            metadata.setdefault('openclaw_unknown_blocks', []).append(
                {'line': line_no, 'block': block}
            )

        if msg_id and message_events:
            message_event_index[msg_id] = message_events[-1].event_id

    if goal is None and first_user_text and not traj.goal:
        traj.goal = first_user_text[:512]
    if not saw_session_header:
        metadata['openclaw_session_header_missing'] = True
    return traj


def _convert_openclaw_trajectory_records(
    records: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    """Convert OpenClaw runtime trajectory JSONL records."""
    first = records[0] if records else {}
    native_trace_id = _opt_str(first.get('traceId') or first.get('sessionId'))
    default_agent = _openclaw_agent_name(first.get('sessionKey')) or 'openclaw-agent'
    traj = _base_trajectory(
        trace_id=trace_id or native_trace_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
        fallback_framework='openclaw',
        metadata={
            'source_format': 'openclaw',
            'openclaw_log_format': 'runtime_trajectory',
            'openclaw_schema_version': first.get('schemaVersion'),
            'openclaw_trace_id': native_trace_id,
            'openclaw_session_id': first.get('sessionId'),
            'openclaw_session_key': first.get('sessionKey'),
            'openclaw_run_id': first.get('runId'),
            'openclaw_workspace_dir': first.get('workspaceDir'),
            'openclaw_provider': first.get('provider'),
            'openclaw_model_id': first.get('modelId'),
            'openclaw_model_api': first.get('modelApi'),
        },
    )
    metadata: Dict[str, Any] = traj.metadata
    step_index = 0
    first_user_text: Optional[str] = None
    saw_started_at = False
    tool_event_index: Dict[str, str] = {}

    def _emit(**kwargs: Any) -> AgentEvent:
        nonlocal step_index
        if kwargs.get('timestamp') is None:
            kwargs.pop('timestamp', None)
        event = traj.add_event(AgentEvent(trace_id=traj.trace_id, **kwargs))
        step_index += 1
        return event

    for line_no, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        rtype = _opt_str(record.get('type')) or ''
        data = record.get('data') if isinstance(record.get('data'), dict) else {}
        timestamp = _parse_openclaw_ts(record.get('ts'))
        common_metadata = {
            'source_format': 'openclaw',
            'openclaw_log_format': 'runtime_trajectory',
            'openclaw_line': line_no,
            'openclaw_type': rtype,
            'openclaw_seq': record.get('seq'),
            'openclaw_source_seq': record.get('sourceSeq'),
            'openclaw_session_id': record.get('sessionId'),
            'openclaw_session_key': record.get('sessionKey'),
            'openclaw_run_id': record.get('runId'),
            'openclaw_thread_id': data.get('threadId'),
            'openclaw_turn_id': data.get('turnId'),
        }

        if rtype == 'session.started':
            if timestamp is not None and not saw_started_at:
                traj.started_at = timestamp
                saw_started_at = True
            metadata.update(
                {
                    'openclaw_session_id': record.get('sessionId'),
                    'openclaw_session_key': record.get('sessionKey'),
                    'openclaw_run_id': record.get('runId'),
                    'openclaw_workspace_dir': record.get('workspaceDir') or data.get('workspaceDir'),
                    'openclaw_provider': record.get('provider'),
                    'openclaw_model_id': record.get('modelId'),
                    'openclaw_model_api': record.get('modelApi'),
                    'openclaw_thread_id': data.get('threadId'),
                    'openclaw_auth_profile_id': data.get('authProfileId'),
                    'openclaw_tool_count': data.get('toolCount'),
                }
            )
            default_agent = _openclaw_agent_name(record.get('sessionKey')) or default_agent
            continue

        if rtype == 'session.ended':
            if timestamp is not None:
                traj.ended_at = timestamp
            metadata['openclaw_end_status'] = data.get('status')
            metadata['openclaw_timed_out'] = data.get('timedOut')
            metadata['openclaw_yield_detected'] = data.get('yieldDetected')
            if data.get('promptError'):
                metadata['openclaw_prompt_error'] = data.get('promptError')
            continue

        if rtype == 'context.compiled':
            metadata['openclaw_context_compiled_seen'] = True
            metadata['openclaw_last_context_tool_count'] = data.get('toolCount')
            metadata['openclaw_last_context_history_length'] = data.get('historyLength')
            continue

        if rtype == 'prompt.submitted':
            prompt = _opt_str(data.get('prompt')) or ''
            if prompt and first_user_text is None:
                first_user_text = prompt.strip()
            _emit(
                agent_name='user',
                event_type=EventType.OBSERVATION,
                module='conversation',
                step_index=step_index,
                output=prompt,
                timestamp=timestamp,
                metadata={**common_metadata, 'openclaw_images_count': data.get('imagesCount')},
            )
            continue

        if rtype == 'tool.call':
            tool_call_id = _opt_str(data.get('toolCallId'))
            tool_name = _opt_str(data.get('name')) or 'tool'
            event = _emit(
                agent_name=tool_name,
                event_type=EventType.TOOL_CALL,
                module='tool',
                step_index=step_index,
                input=data.get('arguments'),
                timestamp=timestamp,
                metadata={
                    **common_metadata,
                    'openclaw_tool_call_id': tool_call_id,
                    'openclaw_tool_use_id': tool_call_id,
                    'openclaw_caller': default_agent,
                },
            )
            if tool_call_id:
                tool_event_index[tool_call_id] = event.event_id
            continue

        if rtype == 'tool.result':
            tool_call_id = _opt_str(data.get('toolCallId'))
            tool_name = _opt_str(data.get('name')) or 'tool'
            success = data.get('success')
            result_text = _openclaw_content_items_text(data.get('contentItems'))
            is_error = success is False
            _emit(
                agent_name=tool_name,
                event_type=EventType.TOOL_RESULT,
                module='tool',
                step_index=step_index,
                output=None if is_error else result_text,
                error=result_text if is_error else None,
                timestamp=timestamp,
                parent_event_id=tool_event_index.get(tool_call_id or ''),
                metadata={
                    **common_metadata,
                    'openclaw_tool_call_id': tool_call_id,
                    'openclaw_tool_use_id': tool_call_id,
                    'openclaw_success': success,
                },
            )
            continue

        if rtype == 'model.completed':
            assistant_text = _openclaw_assistant_text(data)
            _emit(
                agent_name=default_agent,
                event_type=EventType.LLM_RESPONSE,
                module='conversation',
                step_index=step_index,
                output=assistant_text,
                timestamp=timestamp,
                metadata={
                    **common_metadata,
                    'openclaw_timed_out': data.get('timedOut'),
                    'openclaw_finish_reason': data.get('finishReason'),
                },
            )
            continue

        metadata.setdefault('openclaw_unknown_trajectory_types', []).append(
            {'line': line_no, 'type': rtype}
        )

    if goal is None and first_user_text and not traj.goal:
        traj.goal = first_user_text[:512]
    return traj


def _openclaw_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _openclaw_block_text(block: Dict[str, Any]) -> Optional[str]:
    if isinstance(block.get('text'), str):
        return cast(str, block['text'])
    inner = block.get('content')
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        parts: List[str] = []
        for sub in inner:
            if isinstance(sub, dict) and isinstance(sub.get('text'), str):
                parts.append(cast(str, sub['text']))
            elif isinstance(sub, str):
                parts.append(sub)
        if parts:
            return '\n'.join(parts)
    return None


def _openclaw_tool_call_id(
    block: Dict[str, Any], message: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    message = message or {}
    return _opt_str(
        block.get('tool_use_id')
        or block.get('toolUseId')
        or block.get('tool_call_id')
        or block.get('toolCallId')
        or block.get('id')
        or message.get('tool_use_id')
        or message.get('toolUseId')
        or message.get('tool_call_id')
        or message.get('toolCallId')
    )


def _openclaw_content_items_text(items: Any) -> Optional[str]:
    if not isinstance(items, list):
        return _opt_str(items)
    parts: List[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = _opt_str(item.get('text') or item.get('content'))
            if text:
                parts.append(text)
    return '\n'.join(parts) if parts else None


def _openclaw_assistant_text(data: Dict[str, Any]) -> Optional[str]:
    texts = data.get('assistantTexts')
    if isinstance(texts, list):
        parts = [text for text in texts if isinstance(text, str) and text.strip()]
        if parts:
            return '\n'.join(parts)
    snapshot = data.get('messagesSnapshot')
    if isinstance(snapshot, list):
        for message in reversed(snapshot):
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                continue
            parts: List[str] = []
            for block in _openclaw_blocks(message.get('content')):
                text = _openclaw_block_text(block)
                if text:
                    parts.append(text)
            if parts:
                return '\n'.join(parts)
    return None


def _openclaw_agent_name(session_key: Any) -> Optional[str]:
    text = _opt_str(session_key)
    if not text:
        return None
    return text.rsplit(':', 1)[-1] or text


def _parse_openclaw_ts(value: Any) -> Optional[datetime]:
    """Parse OpenClaw ISO timestamps and epoch seconds/milliseconds.

    Returns ``None`` on any failure so the AgentEvent default factory fires.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp = timestamp / 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _convert_claude_code_records(
    records: Sequence[Dict[str, Any]],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    """Convert Claude Code project/session JSONL exports.

    Claude Code stores sessions under
    ``~/.claude/projects/<cwd-as-dashes>/<sessionId>.jsonl``. The JSONL stream
    mixes real transcript rows (``type=user`` / ``type=assistant`` with a
    ``message`` object) with UI/control rows such as ``mode``,
    ``permission-mode``, ``ai-title``, ``last-prompt``, attachments, and
    file-history snapshots. The importer is intentionally selective: only user
    text, assistant text, assistant ``tool_use`` blocks, and user
    ``tool_result`` blocks become events. Control rows are counted in metadata
    so ingestion remains auditable without flooding downstream diagnosis with
    UI noise.
    """

    first_session_id = _claude_code_first_session_id(records)
    traj = _base_trajectory(
        trace_id=trace_id or first_session_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
        fallback_framework='claude_code',
        metadata={'source_format': 'claude_code'},
    )
    metadata: Dict[str, Any] = traj.metadata
    step_index = 0
    first_user_text: Optional[str] = None
    default_agent = 'claude-code'
    ignored_counts: Dict[str, int] = {}
    tool_name_by_id: Dict[str, str] = {}
    tool_event_by_id: Dict[str, str] = {}
    message_event_index: Dict[str, str] = {}
    assistant_message_parent_by_id: Dict[str, Optional[str]] = {}
    message_block_index_by_id: Dict[str, int] = {}

    def _emit(**kwargs: Any) -> AgentEvent:
        nonlocal step_index
        if kwargs.get('timestamp') is None:
            kwargs.pop('timestamp', None)
        event = traj.add_event(AgentEvent(trace_id=traj.trace_id, **kwargs))
        step_index += 1
        return event

    def _note_ignored(record_type: Any) -> None:
        key = str(record_type or '<missing>')
        ignored_counts[key] = ignored_counts.get(key, 0) + 1

    def _emit_for_message(
        message_events: List[AgentEvent], **kwargs: Any
    ) -> AgentEvent:
        event = _emit(**kwargs)
        message_events.append(event)
        return event

    for line_no, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        rtype = _opt_str(record.get('type')) or ''
        session_id = _opt_str(record.get('sessionId'))
        if session_id and trace_id is None and traj.trace_id.startswith('trace_'):
            traj.trace_id = session_id
            for existing in traj.events:
                existing.trace_id = session_id
        _claude_code_update_session_metadata(metadata, record)

        message = record.get('message')
        if rtype not in {'user', 'assistant'} or not isinstance(message, dict):
            _note_ignored(rtype)
            continue

        role = _opt_str(message.get('role')) or rtype
        message_id = _opt_str(message.get('id'))
        timestamp = _parse_openclaw_ts(record.get('timestamp'))
        native_uuid = _opt_str(record.get('uuid'))
        native_parent_uuid = _opt_str(record.get('parentUuid'))
        native_parent_event_id = message_event_index.get(native_parent_uuid or '')
        base_meta = _claude_code_record_metadata(record, line_no=line_no, role=role)
        message_events: List[AgentEvent] = []

        blocks = _claude_code_blocks(message.get('content'))

        if role == 'user' and _claude_code_is_control_user_record(record, blocks):
            _note_ignored('user.control')
            continue

        if role == 'user':
            for block_index, block in enumerate(blocks):
                message_block_index = block_index
                if message_id:
                    message_block_index = message_block_index_by_id.get(message_id, 0)
                    message_block_index_by_id[message_id] = message_block_index + 1
                btype = _opt_str(block.get('type')) or 'text'
                if btype == 'tool_result':
                    tool_use_id = _opt_str(block.get('tool_use_id'))
                    tool_name = tool_name_by_id.get(tool_use_id or '', 'tool')
                    is_error = bool(block.get('is_error'))
                    result_text = _claude_code_block_text(block)
                    result_output = (
                        result_text
                        if result_text is not None
                        else block.get('content')
                    )
                    _emit_for_message(
                        message_events,
                        agent_name=tool_name,
                        event_type=EventType.TOOL_RESULT,
                        module='tool',
                        step_index=step_index,
                        parent_event_id=tool_event_by_id.get(
                            tool_use_id or '', native_parent_event_id
                        ),
                        output=None if is_error else result_output,
                        error=result_text if is_error else None,
                        timestamp=timestamp,
                        metadata={
                            **base_meta,
                            'claude_code_block_index': message_block_index,
                            'claude_code_record_block_index': block_index,
                            'claude_code_block_type': btype,
                            'claude_code_tool_use_id': tool_use_id,
                            'claude_code_tool_call_id': tool_use_id,
                            'claude_code_is_error': is_error,
                        },
                    )
                    continue

                text = _claude_code_block_text(block)
                if not text or not text.strip():
                    continue
                if first_user_text is None:
                    first_user_text = text.strip()
                _emit_for_message(
                    message_events,
                    agent_name='user',
                    event_type=EventType.OBSERVATION,
                    module='conversation',
                    step_index=step_index,
                    parent_event_id=native_parent_event_id,
                    output=text,
                    timestamp=timestamp,
                    metadata={
                        **base_meta,
                        'claude_code_block_index': message_block_index,
                        'claude_code_record_block_index': block_index,
                        'claude_code_block_type': btype,
                    },
                )
            if native_uuid and message_events:
                message_event_index[native_uuid] = message_events[-1].event_id
            continue

        if role == 'assistant':
            if message_id and message_id not in assistant_message_parent_by_id:
                assistant_message_parent_by_id[message_id] = native_parent_event_id
            parent_event_id = (
                assistant_message_parent_by_id.get(message_id or '')
                if message_id
                else native_parent_event_id
            )
            for block_index, block in enumerate(blocks):
                message_block_index = block_index
                if message_id:
                    message_block_index = message_block_index_by_id.get(message_id, 0)
                    message_block_index_by_id[message_id] = message_block_index + 1
                btype = _opt_str(block.get('type')) or 'text'
                if btype == 'thinking':
                    thinking = _opt_str(block.get('thinking'))
                    if not thinking:
                        continue
                    event = _emit_for_message(
                        message_events,
                        agent_name=default_agent,
                        event_type=EventType.REFLECTION,
                        module='reasoning',
                        step_index=step_index,
                        parent_event_id=parent_event_id,
                        output=thinking,
                        timestamp=timestamp,
                        metadata={
                            **base_meta,
                            'claude_code_block_index': message_block_index,
                            'claude_code_record_block_index': block_index,
                            'claude_code_block_type': btype,
                            'claude_code_thinking_signature_present': bool(
                                block.get('signature')
                            ),
                        },
                    )
                    parent_event_id = event.event_id
                    if message_id:
                        assistant_message_parent_by_id[message_id] = parent_event_id
                    continue

                if btype == 'text':
                    text = _claude_code_block_text(block)
                    if not text or not text.strip():
                        continue
                    event = _emit_for_message(
                        message_events,
                        agent_name=default_agent,
                        event_type=EventType.LLM_RESPONSE,
                        module='conversation',
                        step_index=step_index,
                        parent_event_id=parent_event_id,
                        output=text,
                        timestamp=timestamp,
                        metadata={
                            **base_meta,
                            'claude_code_block_index': message_block_index,
                            'claude_code_record_block_index': block_index,
                            'claude_code_block_type': btype,
                        },
                    )
                    parent_event_id = event.event_id
                    if message_id:
                        assistant_message_parent_by_id[message_id] = parent_event_id
                    continue

                if btype == 'tool_use':
                    tool_name = _opt_str(block.get('name')) or 'tool'
                    tool_id = _opt_str(block.get('id'))
                    if tool_id:
                        tool_name_by_id[tool_id] = tool_name
                    event = _emit_for_message(
                        message_events,
                        agent_name=tool_name,
                        event_type=EventType.TOOL_CALL,
                        module='tool',
                        step_index=step_index,
                        parent_event_id=parent_event_id,
                        input=block.get('input'),
                        timestamp=timestamp,
                        metadata={
                            **base_meta,
                            'claude_code_block_index': message_block_index,
                            'claude_code_record_block_index': block_index,
                            'claude_code_block_type': btype,
                            'claude_code_tool_use_id': tool_id,
                            'claude_code_tool_call_id': tool_id,
                            'claude_code_caller': default_agent,
                        },
                    )
                    if tool_id:
                        tool_event_by_id[tool_id] = event.event_id
                    continue

                metadata.setdefault('claude_code_unknown_blocks', []).append(
                    {'line': line_no, 'type': btype}
                )

            if native_uuid and message_events:
                message_event_index[native_uuid] = message_events[-1].event_id
            continue

        _note_ignored(f'{rtype}.{role}')

    if goal is None and first_user_text and not traj.goal:
        traj.goal = first_user_text[:512]
    if traj.events:
        traj.started_at = min(event.timestamp for event in traj.events)
        traj.ended_at = max(event.timestamp for event in traj.events)
    metadata['claude_code_ignored_record_counts'] = ignored_counts
    metadata['claude_code_event_count'] = len(traj.events)
    return traj


def _claude_code_first_session_id(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    for record in records:
        if isinstance(record, dict):
            session_id = _opt_str(record.get('sessionId'))
            if session_id:
                return session_id
    return None


def _claude_code_update_session_metadata(
    metadata: Dict[str, Any], record: Dict[str, Any]
) -> None:
    for native_key, meta_key in (
        ('sessionId', 'claude_code_session_id'),
        ('cwd', 'claude_code_cwd'),
        ('version', 'claude_code_version'),
        ('gitBranch', 'claude_code_git_branch'),
        ('entrypoint', 'claude_code_entrypoint'),
        ('permissionMode', 'claude_code_permission_mode'),
        ('userType', 'claude_code_user_type'),
    ):
        value = record.get(native_key)
        if value is not None and meta_key not in metadata:
            metadata[meta_key] = value


def _claude_code_record_metadata(
    record: Dict[str, Any],
    *,
    line_no: int,
    role: str,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        'source_format': 'claude_code',
        'claude_code_line': line_no,
        'claude_code_type': record.get('type'),
        'claude_code_role': role,
    }
    for native_key, meta_key in (
        ('uuid', 'claude_code_uuid'),
        ('parentUuid', 'claude_code_parent_uuid'),
        ('sessionId', 'claude_code_session_id'),
        ('promptId', 'claude_code_prompt_id'),
        ('cwd', 'claude_code_cwd'),
        ('version', 'claude_code_version'),
        ('gitBranch', 'claude_code_git_branch'),
        ('isSidechain', 'claude_code_is_sidechain'),
        ('isMeta', 'claude_code_is_meta'),
        ('promptSource', 'claude_code_prompt_source'),
        ('userType', 'claude_code_user_type'),
    ):
        value = record.get(native_key)
        if value is not None:
            metadata[meta_key] = value
    message = record.get('message')
    if isinstance(message, dict) and message.get('id') is not None:
        metadata['claude_code_message_id'] = message.get('id')
    origin = record.get('origin')
    if isinstance(origin, dict) and origin.get('kind') is not None:
        metadata['claude_code_origin_kind'] = origin.get('kind')
    return metadata


def _claude_code_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    if content is None:
        return []
    return [{'type': 'text', 'text': str(content)}]


def _claude_code_block_text(block: Dict[str, Any]) -> Optional[str]:
    for key in ('text', 'content'):
        value = block.get(key)
        if isinstance(value, str):
            return value
    value = block.get('content')
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get('text') or item.get('content')
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return '\n'.join(parts) if parts else None
    if value is not None:
        return str(value)
    return None


def _claude_code_is_control_user_record(
    record: Dict[str, Any],
    blocks: Sequence[Dict[str, Any]],
) -> bool:
    has_tool_result = any(block.get('type') == 'tool_result' for block in blocks)
    if record.get('isMeta') is True:
        return True
    if record.get('isCompactSummary') is True:
        return True
    if record.get('toolUseResult') is not None and not has_tool_result:
        return True
    origin = record.get('origin')
    if isinstance(origin, dict) and origin.get('kind') == 'system':
        return True
    for block in blocks:
        text = (_claude_code_block_text(block) or '').lstrip()
        if text.startswith(
            (
                '<local-command-caveat>',
                '<local-command-stdout>',
                '<local-command-stderr>',
                '<command-name>',
                '<command-message>',
            )
        ):
            return True
    return False


def _hermes_session_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    wrapped = payload.get('hermes_session')
    if isinstance(wrapped, dict):
        session = dict(wrapped)
        for key, value in payload.items():
            if key not in {'hermes_session', 'messages'} and key not in session:
                session[key] = value
        return session
    return {key: value for key, value in payload.items() if key != 'messages'}


def _hermes_metadata_value(key: str, value: Any) -> Any:
    if key == 'system_prompt' and isinstance(value, str) and len(value) > 2000:
        return value[:2000] + '\n[truncated]'
    return value


_HERMES_MESSAGE_METADATA_KEYS = {
    'id',
    'session_id',
    'token_count',
    'finish_reason',
    'reasoning_details',
    'codex_reasoning_items',
    'codex_message_items',
    'platform_message_id',
    'observed',
    'active',
    'compacted',
}


def _hermes_message_metadata(message: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for key in _HERMES_MESSAGE_METADATA_KEYS:
        value = message.get(key)
        if value is not None:
            meta_key = 'hermes_message_id' if key == 'id' else f'hermes_{key}'
            metadata[meta_key] = value
    return metadata


def _convert_hermes_export(
    payload: Dict[str, Any],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
) -> AgentTrajectory:
    """Convert a Hermes (NousResearch hermes-agent) session export.

    Hermes persists sessions in SQLite (``~/.hermes/state.db``). SessionDB
    exports session columns next to ``messages``; each message row uses
    OpenAI-chat-shaped columns: ``role`` / ``content`` / ``tool_calls`` (JSON
    text) / ``tool_call_id`` / ``tool_name`` / ``reasoning`` / ``timestamp``
    (unix epoch). The preferred payload is the native export shape::

        {"id": ..., "source": ..., "started_at": ..., "messages": [...]}

    For compatibility, the importer also accepts the older AgentDebugX wrapper::

        {"hermes_session": {"id": ..., "title": ...}, "messages": [...]}

    Tool calls become ``TOOL_CALL`` events with the function name as
    ``agent_name`` so attribution backends blame the responsible tool, and
    ``role=tool`` rows become ``TOOL_RESULT`` events whose tool name is
    recovered via ``tool_call_id``. Hermes has no explicit error flag on tool
    rows, so a conservative prefix heuristic marks obvious failures and
    records ``hermes_error_heuristic`` in the event metadata.
    """
    session = _hermes_session_metadata(payload)
    messages = payload.get('messages')
    if not isinstance(messages, list):
        raise ConversionError('hermes payload must contain a messages list')

    traj = _base_trajectory(
        trace_id=trace_id or _opt_str(session.get('id')),
        task_id=task_id or _opt_str(session.get('task_id')),
        goal=goal or _opt_str(session.get('title')),
        framework=framework,
        fallback_framework='hermes',
        metadata={'source_format': 'hermes'},
    )
    started_at = _parse_hermes_ts(session.get('started_at'))
    if started_at is not None:
        traj.started_at = started_at
    ended_at = _parse_hermes_ts(session.get('ended_at'))
    if ended_at is not None:
        traj.ended_at = ended_at
    metadata: Dict[str, Any] = traj.metadata
    for key, value in session.items():
        if key == 'messages' or value is None:
            continue
        metadata[f'hermes_{key}'] = _hermes_metadata_value(key, value)

    default_agent = _opt_str(session.get('agent')) or 'hermes-agent'
    step_index = 0
    tool_call_index: Dict[str, str] = {}
    tool_call_event_index: Dict[str, str] = {}
    first_user_text: Optional[str] = None

    def _emit(**kwargs: Any) -> AgentEvent:
        nonlocal step_index
        if kwargs.get('timestamp') is None:
            kwargs.pop('timestamp', None)
        event = traj.add_event(AgentEvent(trace_id=traj.trace_id, **kwargs))
        step_index += 1
        return event

    for idx, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = _opt_str(message.get('role')) or 'agent'
        content = _message_content(message.get('content'))
        timestamp = _parse_hermes_ts(message.get('timestamp'))
        base_meta = {
            'source_format': 'hermes',
            'hermes_index': idx,
            **_hermes_message_metadata(message),
        }

        if role == 'user':
            if content and first_user_text is None:
                first_user_text = content.strip()
            if content:
                _emit(
                    agent_name='user',
                    event_type=EventType.OBSERVATION,
                    module='conversation',
                    step_index=step_index,
                    output=content,
                    timestamp=timestamp,
                    metadata=base_meta,
                )
            continue

        if role == 'assistant':
            reasoning = _opt_str(
                message.get('reasoning') or message.get('reasoning_content')
            )
            parent_event_id: Optional[str] = None
            if reasoning:
                reasoning_event = _emit(
                    agent_name=default_agent,
                    event_type=EventType.REFLECTION,
                    module='reasoning',
                    step_index=step_index,
                    output=reasoning,
                    timestamp=timestamp,
                    metadata=base_meta,
                )
                parent_event_id = reasoning_event.event_id
            if content:
                response_event = _emit(
                    agent_name=default_agent,
                    event_type=EventType.LLM_RESPONSE,
                    module='conversation',
                    step_index=step_index,
                    parent_event_id=parent_event_id,
                    output=content,
                    timestamp=timestamp,
                    metadata={
                        **base_meta,
                        'hermes_finish_reason': message.get('finish_reason'),
                    },
                )
                parent_event_id = response_event.event_id
            for call in _hermes_tool_calls(message.get('tool_calls')):
                function = call.get('function')
                function = function if isinstance(function, dict) else {}
                tool_name = _opt_str(function.get('name')) or 'tool'
                call_id = _opt_str(call.get('id'))
                if call_id:
                    tool_call_index[call_id] = tool_name
                tool_call_event = _emit(
                    agent_name=tool_name,
                    event_type=EventType.TOOL_CALL,
                    module='tool',
                    step_index=step_index,
                    parent_event_id=parent_event_id,
                    input=_hermes_tool_arguments(function.get('arguments')),
                    timestamp=timestamp,
                    metadata={
                        **base_meta,
                        'hermes_tool_call_id': call_id,
                        'hermes_caller': default_agent,
                    },
                )
                if call_id:
                    tool_call_event_index[call_id] = tool_call_event.event_id
            continue

        if role == 'tool':
            call_id = _opt_str(message.get('tool_call_id'))
            tool_name = (
                _opt_str(message.get('tool_name'))
                or tool_call_index.get(call_id or '', '')
                or 'tool'
            )
            is_error = _hermes_looks_like_error(content)
            _emit(
                agent_name=tool_name,
                event_type=EventType.TOOL_RESULT,
                module='tool',
                step_index=step_index,
                parent_event_id=tool_call_event_index.get(call_id or ''),
                output=None if is_error else content,
                error=content if is_error else None,
                timestamp=timestamp,
                metadata={
                    **base_meta,
                    'hermes_tool_call_id': call_id,
                    'hermes_error_heuristic': is_error,
                },
            )
            continue

        if role == 'system':
            metadata.setdefault('hermes_system_prompts', []).append(
                {'index': idx, 'content': (content or '')[:2000]}
            )
            continue

        if content:
            _emit(
                agent_name=role,
                event_type=EventType.OBSERVATION,
                module='conversation',
                step_index=step_index,
                output=content,
                timestamp=timestamp,
                metadata=base_meta,
            )

    if goal is None and first_user_text and not traj.goal:
        traj.goal = first_user_text[:512]
    return traj


def _hermes_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    """Normalize the ``tool_calls`` column: JSON text in SQLite, list in API."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    return []


def _hermes_tool_arguments(raw: Any) -> Any:
    """OpenAI function arguments arrive as a JSON string; surface the object."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


_HERMES_ERROR_PREFIXES = (
    'error',
    'traceback (most recent call last)',
    'exception',
    'fatal:',
    'command failed',
    'command exited with code',
    # Shell-style failures: `bash: ./x.sh: No such file or directory`
    'bash: ',
    'sh: ',
    'zsh: ',
)

# Markers that only count on the FIRST line of a tool result — a result whose
# first line says "command not found" is a shell failure; the same words deep
# inside a long output are usually just quoted text.
_HERMES_ERROR_FIRST_LINE_MARKERS = (
    'command not found',
    'no such file or directory',
    'permission denied',
    'non-zero exit',
)


def _hermes_looks_like_error(content: Optional[str]) -> bool:
    if not content:
        return False
    stripped = content.lstrip()
    head = stripped[:80].lower()
    if any(head.startswith(prefix) for prefix in _HERMES_ERROR_PREFIXES):
        return True
    first_line = stripped.splitlines()[0].lower() if stripped else ''
    return any(marker in first_line for marker in _HERMES_ERROR_FIRST_LINE_MARKERS)


def _parse_hermes_ts(value: Any) -> Optional[datetime]:
    """Hermes stores REAL unix epochs; the REST API may emit ISO strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        return _parse_openclaw_ts(value)
    return None


def _event_type_for_role(role: str) -> EventType:
    role_l = role.lower()
    if role_l in {'user', 'human'}:
        return EventType.OBSERVATION
    if role_l in {'system'}:
        return EventType.RUN_START
    if role_l in {'tool', 'function'}:
        return EventType.TOOL_RESULT
    return EventType.AGENT_STEP


def _event_type_for_span(span_type: str) -> EventType:
    text = span_type.lower()
    if 'tool' in text or 'function' in text:
        return EventType.TOOL_CALL if 'start' in text or 'call' in text else EventType.TOOL_RESULT
    if 'generation' in text or 'llm' in text or 'chat' in text:
        return EventType.LLM_CALL
    if 'handoff' in text:
        return EventType.HANDOFF
    return EventType.AGENT_STEP


def _event_type_for_crewai(name: str) -> EventType:
    text = name.lower()
    if 'tool' in text:
        return EventType.TOOL_RESULT if 'finished' in text or 'completed' in text else EventType.TOOL_CALL
    if 'llm' in text:
        return EventType.LLM_RESPONSE if 'completed' in text or 'finished' in text else EventType.LLM_CALL
    if 'task' in text:
        return EventType.PLAN if 'started' in text else EventType.AGENT_STEP
    return EventType.AGENT_STEP


def _event_type_for_langgraph(name: str) -> EventType:
    text = name.lower()
    if 'tool' in text:
        return EventType.TOOL_RESULT if 'end' in text or 'error' in text else EventType.TOOL_CALL
    if 'llm' in text or 'chat' in text:
        return EventType.LLM_RESPONSE if 'end' in text else EventType.LLM_CALL
    if 'chain' in text or 'graph' in text:
        return EventType.AGENT_STEP
    return EventType.AGENT_STEP


def _extract_goal(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ('goal', 'instruction', 'task', 'objective', 'prompt'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _goal_from_messages(messages: Sequence[Dict[str, Any]]) -> Optional[str]:
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get('role') or message.get('from') or '').lower()
        content = _message_content(message.get('content') or message.get('value') or message.get('text'))
        if role in {'user', 'human'} and content:
            return content[:300]
    return None


def _task_id_from_webshop(payload: Dict[str, Any]) -> Optional[str]:
    for key in ('task_id', 'item_id', 'id', 'asin'):
        value = _opt_str(payload.get(key))
        if value:
            return value
    url = _opt_str(payload.get('url'))
    if url:
        return url.rstrip('/').split('/')[-1] or None
    return None


def _message_content(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get('text') or item.get('content') or item))
            else:
                parts.append(str(item))
        return '\n'.join(parts)
    return str(content)


def _split_thought_action(text: str) -> Tuple[Optional[str], Optional[str], str]:
    if 'Action:' not in text:
        return None, None, text.strip()
    before, after = text.split('Action:', 1)
    thought = before.replace('Thought:', '', 1).strip() if 'Thought:' in before else before.strip()
    action = after.strip()
    return thought or None, action or None, ''


def _classify_action_event_type(action: str) -> EventType:
    lowered = action.lower()
    if lowered.startswith(('search', 'click', 'open', 'look', 'go to', 'goto', 'use', 'take', 'put', 'cool', 'heat', 'clean')):
        return EventType.TOOL_CALL
    if lowered.startswith(('finish', 'done', 'answer', 'respond', 'say', 'report')):
        return EventType.AGENT_STEP
    return EventType.AGENT_STEP


def _classify_observation_event(content: str) -> EventType:
    lowered = content.lower()
    if 'previous reflections:' in lowered or 'current observation:' in lowered:
        return EventType.OBSERVATION
    if 'error' in lowered or 'exception' in lowered:
        return EventType.ERROR
    return EventType.OBSERVATION


def _coerce_event_type(value: Any) -> EventType:
    if isinstance(value, EventType):
        return value
    text = str(value or '').lower().replace('_', '.')
    for event_type in EventType:
        if text == event_type.value:
            return event_type
    aliases = {
        'message': EventType.AGENT_STEP,
        'assistant': EventType.AGENT_STEP,
        'user': EventType.OBSERVATION,
        'tool': EventType.TOOL_CALL,
        'tool.result': EventType.TOOL_RESULT,
        'tool_result': EventType.TOOL_RESULT,
        'observation': EventType.OBSERVATION,
    }
    return aliases.get(text, EventType.AGENT_STEP)


def _module_for_event_type(event_type: EventType) -> str:
    if event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}:
        return 'action'
    if event_type in {EventType.LLM_CALL, EventType.LLM_RESPONSE}:
        return 'llm'
    if event_type in {EventType.PLAN, EventType.AGENT_STEP}:
        return 'planning'
    if event_type == EventType.OBSERVATION:
        return 'environment'
    if event_type == EventType.HANDOFF:
        return 'multiagent'
    return 'runtime'


def _looks_webshop_page(payload: Any) -> bool:
    return isinstance(payload, dict) and (
        'page' in payload or 'url' in payload
    ) and any(key in payload for key in ('content', 'text', 'goal', 'observation'))


def _looks_crewai_event(item: Dict[str, Any]) -> bool:
    text = str(item.get('event') or item.get('type') or item.get('name') or '').lower()
    return 'crewai' in text or 'task' in text or 'crew' in item


def _looks_crewai_event_list(items: Sequence[Dict[str, Any]]) -> bool:
    return bool(items) and all(_looks_crewai_event(item) for item in items)


def _looks_langgraph_callback(item: Dict[str, Any]) -> bool:
    text = str(item.get('event') or item.get('type') or item.get('name') or '').lower()
    return (
        text.startswith('on_')
        or 'run_id' in item
        or 'parent_run_id' in item
    ) and any(token in text for token in ('chain', 'tool', 'llm', 'chat', 'graph'))


def _looks_openclaw_records(items: Sequence[Dict[str, Any]]) -> bool:
    """True when ``items`` look like one of OpenClaw's JSONL streams.

    Current OpenClaw writes two different log families with overlapping
    ``type`` fields. The session transcript log has a ``session`` header and
    ``message`` rows; the runtime trajectory log has
    ``traceSchema=openclaw-trajectory`` rows. Both need OpenClaw-specific
    conversion because the generic event-list importer would miss nested
    message/tool payloads.
    """
    if not items:
        return False
    if _looks_openclaw_trajectory_records(items):
        return True
    saw_session_header = False
    saw_openclaw_message = False
    for item in items:
        rtype = item.get('type')
        if rtype == 'session' and isinstance(item.get('id'), str):
            saw_session_header = True
            continue
        if rtype == 'message':
            message = item.get('message')
            if isinstance(message, dict) and isinstance(message.get('role'), str):
                saw_openclaw_message = True
    return saw_session_header or saw_openclaw_message


def _looks_openclaw_trajectory_records(items: Sequence[Dict[str, Any]]) -> bool:
    if not items:
        return False
    return all(item.get('traceSchema') == 'openclaw-trajectory' for item in items)


def _looks_claude_code_records(items: Sequence[Dict[str, Any]]) -> bool:
    """True for Claude Code ``~/.claude/projects/.../*.jsonl`` streams."""
    if not items:
        return False
    saw_session_record = False
    saw_claude_message = False
    for item in items:
        if not isinstance(item, dict):
            return False
        rtype = item.get('type')
        if rtype in {
            'mode',
            'permission-mode',
            'file-history-snapshot',
            'ai-title',
            'last-prompt',
            'attachment',
            'system',
        } and 'sessionId' in item:
            saw_session_record = True
            continue
        if rtype in {'user', 'assistant'}:
            message = item.get('message')
            if (
                isinstance(message, dict)
                and message.get('role') == rtype
                and 'uuid' in item
                and 'sessionId' in item
            ):
                saw_claude_message = True
    return saw_claude_message and saw_session_record


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
