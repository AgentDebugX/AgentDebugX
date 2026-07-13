"""GAIA Open Deep Research trajectory import adapter.

This adapter converts trajectory JSON files emitted by the GAIA
Open Deep Research harness into AgentDebugX's portable ``AgentTrajectory`` IR.
It is intentionally limited to the GAIA/ODR export shape; downstream diagnose,
attribution, and recovery stages should consume only the normalized IR.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from agentdebug.schema import AgentEvent, AgentTrajectory, EventType
from agentdebug.ingest.adapters.importers import ConversionError

ROLE_RE = re.compile(r"role=<MessageRole\.[^:]+: '([^']+)'>")
CONTENT_RE = re.compile(r'content=(\[.*\]), tool_calls=', re.S)
TOOL_LIST_RE = re.compile(r'Calling tools:\n(\[.*\])', re.S)
FIRST_TOOL_NAME_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(')


@dataclass
class ParsedMessage:
    role: str
    text: str


def convert_gaia_odr_file(
    path: Union[str, Path],
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Load a GAIA Open Deep Research trajectory JSON file."""

    src_path = Path(path)
    if not src_path.exists():
        raise ConversionError(f'input file does not exist: {src_path}')
    payload = json.loads(src_path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ConversionError('GAIA ODR trajectory must be a JSON object')
    return convert_gaia_odr_payload(
        payload,
        source_path=src_path,
        trace_id=trace_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
    )


def convert_gaia_odr_payload(
    payload: Dict[str, Any],
    *,
    source_path: Optional[Union[str, Path]] = None,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Normalize one GAIA Open Deep Research trajectory payload."""

    task = payload.get('task') or {}
    if not isinstance(task, dict):
        task = {}

    resolved_trace_id = trace_id or payload.get('task_id') or (
        Path(source_path).stem if source_path is not None else None
    )
    if not resolved_trace_id:
        raise ConversionError('GAIA ODR trajectory is missing task_id/trace_id')

    resolved_task_id = task_id or resolved_trace_id
    resolved_goal = goal or task.get('Question') or payload.get('prediction') or resolved_trace_id

    trajectory_kwargs: Dict[str, Any] = {
        'trace_id': str(resolved_trace_id),
        'task_id': str(resolved_task_id),
        'goal': resolved_goal,
        'framework': framework or 'gaia-open-deep-research',
        'metadata': {
            'gaia_level': payload.get('level'),
            'gaia_status': payload.get('status'),
            'gaia_prediction': payload.get('prediction'),
            'gaia_gold': payload.get('gold'),
            'gaia_scores': payload.get('scores'),
            'elapsed_seconds': payload.get('elapsed_seconds'),
            'token_counts': payload.get('token_counts'),
            'task': task,
            'source_path': str(source_path) if source_path is not None else None,
            'source_format': 'gaia_odr',
        },
    }
    if payload.get('start_time') is not None:
        trajectory_kwargs['started_at'] = payload.get('start_time')
    if payload.get('end_time') is not None:
        trajectory_kwargs['ended_at'] = payload.get('end_time')

    traj = AgentTrajectory(**trajectory_kwargs)

    step_index = 0
    traj.add_event(
        AgentEvent(
            trace_id=traj.trace_id,
            agent_name='runner',
            event_type=EventType.RUN_START,
            module='orchestration',
            step_index=step_index,
            output=f'Start GAIA task {resolved_trace_id}',
            metadata={'source': 'gaia_odr'},
        )
    )

    for blob in payload.get('intermediate_steps') or []:
        if not isinstance(blob, str):
            continue
        try:
            parsed = parse_message_blob(blob)
        except Exception:
            step_index += 1
            traj.add_event(
                AgentEvent(
                    trace_id=traj.trace_id,
                    agent_name='parser',
                    event_type=EventType.OBSERVATION,
                    module='conversion',
                    step_index=step_index,
                    output=str(blob)[:4000],
                    metadata={'conversion_warning': 'unparsed_intermediate_step'},
                )
            )
            continue

        step_index += 1
        traj.add_event(
            _message_to_event(
                parsed,
                trace_id=traj.trace_id,
                step_index=step_index,
            )
        )

    step_index += 1
    end_type = EventType.RUN_END if payload.get('status') == 'ok' else EventType.ERROR
    traj.add_event(
        AgentEvent(
            trace_id=traj.trace_id,
            agent_name='runner',
            event_type=end_type,
            module='orchestration',
            step_index=step_index,
            output=payload.get('prediction'),
            error=payload.get('error'),
            metadata={
                'gold': payload.get('gold'),
                'scores': payload.get('scores'),
            },
        )
    )
    return traj


def parse_message_blob(blob: str) -> ParsedMessage:
    """Parse the stringified smolagents ``ChatMessage`` representation."""

    role_match = ROLE_RE.search(blob)
    content_match = CONTENT_RE.search(blob)
    if not role_match or not content_match:
        raise ConversionError('could not parse ChatMessage blob')
    role = role_match.group(1)
    content_blocks = ast.literal_eval(content_match.group(1))
    parts = []
    for block in content_blocks:
        if isinstance(block, dict) and isinstance(block.get('text'), str):
            parts.append(block['text'])
    return ParsedMessage(role=role, text='\n'.join(parts))


def parse_tool_call_text(text: str) -> Tuple[str, Any]:
    match = TOOL_LIST_RE.search(text)
    if not match:
        return 'tool', text
    tool_specs = ast.literal_eval(match.group(1))
    if not tool_specs:
        return 'tool', text
    spec = tool_specs[0]
    function = spec.get('function') or {}
    tool_name = function.get('name') or 'tool'
    arguments = function.get('arguments')
    if tool_name == 'python_interpreter' and isinstance(arguments, str):
        inner = FIRST_TOOL_NAME_RE.search(arguments)
        if inner:
            return inner.group(1), arguments
    return tool_name, arguments


def classify_tool_response(text: str) -> Tuple[Any, Optional[str]]:
    lowered = text.lower()
    if 'traceback' in lowered or 'exception' in lowered or 'error:' in lowered:
        return None, text
    return text, None


def _message_to_event(
    parsed: ParsedMessage,
    *,
    trace_id: str,
    step_index: int,
) -> AgentEvent:
    role = parsed.role
    text = parsed.text

    if role == 'system':
        return AgentEvent(
            trace_id=trace_id,
            agent_name='system',
            event_type=EventType.OBSERVATION,
            module='prompting',
            step_index=step_index,
            output=text,
        )
    if role == 'user':
        return AgentEvent(
            trace_id=trace_id,
            agent_name='user',
            event_type=EventType.OBSERVATION,
            module='conversation',
            step_index=step_index,
            output=text,
        )
    if role == 'assistant':
        module = 'action' if '<code>' in text else 'reasoning'
        return AgentEvent(
            trace_id=trace_id,
            agent_name='assistant',
            event_type=EventType.LLM_RESPONSE,
            module=module,
            step_index=step_index,
            output=text,
        )
    if role == 'tool-call':
        tool_name, tool_input = parse_tool_call_text(text)
        return AgentEvent(
            trace_id=trace_id,
            agent_name=tool_name,
            event_type=EventType.TOOL_CALL,
            module='tool',
            step_index=step_index,
            input=tool_input,
            output=text,
        )
    if role == 'tool-response':
        output, error = classify_tool_response(text)
        return AgentEvent(
            trace_id=trace_id,
            agent_name='tool',
            event_type=EventType.TOOL_RESULT if error is None else EventType.ERROR,
            module='tool',
            step_index=step_index,
            output=output,
            error=error,
        )
    return AgentEvent(
        trace_id=trace_id,
        agent_name=role,
        event_type=EventType.OBSERVATION,
        module='conversation',
        step_index=step_index,
        output=text,
    )


__all__ = [
    'ParsedMessage',
    'classify_tool_response',
    'convert_gaia_odr_file',
    'convert_gaia_odr_payload',
    'parse_message_blob',
    'parse_tool_call_text',
]
