"""OpenAI-compatible model executor for trajectory reruns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from agentdebug.rerun.executors.base import RerunResult
from agentdebug.rerun.request import RerunRequest
from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType, new_id

_ROLLOUT_PROMPT = (
    'You are rerunning an agent task after a failed attempt was diagnosed. '
    'Execute a fresh rollout using the retry directive. Return the observable '
    'trajectory, not analysis of what you would do. Do not copy the failed '
    'suffix as the new result.\n'
    'Return JSON only: {"summary": "<outcome>", "success": <bool>, '
    '"events": [{"agent_name": "<agent>", "event_type": "<event type>", '
    '"module": "<module>", "step_index": <int>, "input": <value>, '
    '"output": <value>, "error": <string or null>, "metadata": {}}]}. '
    'Events must be chronological and contain enough detail to inspect the '
    'new execution. Never include hidden chain-of-thought.'
)

_FAILURE_ONLY_METADATA_KEYS = {
    'expected_outcome',
    'expected_root_cause_agent',
    'expected_root_cause_event_id',
    'expected_root_cause_step_index',
    'failure_family',
}

_FAILURE_ONLY_METADATA_PREFIXES = (
    'expected_failure_',
    'expected_root_cause_',
)


@dataclass(frozen=True)
class RolloutContext:
    """Executor-only source context excluded from the portable request schema."""

    trajectory: AgentTrajectory
    start_event_id: Optional[str] = None
    prompt_override: Optional[str] = None


class LLMContinuationExecutor:
    """Generate a new trajectory with an OpenAI-compatible LLM client.

    This executor performs a model rollout over recorded context. It does not
    restore arbitrary third-party tool processes; framework-specific executors
    can implement the same ``RerunExecutor`` protocol when live tools are needed.
    """

    id = 'llm_continuation'

    def __init__(
        self,
        llm: LLMClient,
        context: RolloutContext,
        *,
        max_tokens: int = 8192,
    ) -> None:
        self.llm = llm
        self.context = context
        self.max_tokens = max_tokens

    def run(self, request: RerunRequest) -> RerunResult:
        prompt = self.context.prompt_override or build_rollout_prompt(
            request,
            self.context,
        )
        completion = self.llm.complete(
            messages=[
                {'role': 'system', 'content': _ROLLOUT_PROMPT},
                {'role': 'user', 'content': prompt},
            ],
            response_format={'type': 'json_object'},
            temperature=0.2,
            max_tokens=self.max_tokens,
        )
        payload = extract_json_block(completion.text)
        if not isinstance(payload, dict):
            raise ValueError('rerun model returned no valid JSON object')
        rerun = trajectory_from_rollout(
            payload,
            source=self.context.trajectory,
            request=request,
            parent_event_id=(
                request.checkpoint.event_id
                if request.checkpoint.policy != 'from_start'
                else None
            ),
        )
        return RerunResult(
            request=request,
            trajectory=rerun,
            metadata={
                'executor': self.id,
                'model': self.llm.model,
                'checkpoint_policy': request.checkpoint.policy,
                'source_trace_id': self.context.trajectory.trace_id,
                'response_summary': str(payload.get('summary') or ''),
                'reported_success': payload.get('success'),
                'usage': completion.raw.get('usage'),
                'finish_reason': _finish_reason(completion.raw),
            },
        )


def build_rollout_prompt(request: RerunRequest, context: RolloutContext) -> str:
    """Build full-task or checkpoint continuation context for a rerun."""

    trajectory = context.trajectory
    events = list(trajectory.events)
    start_index = 0
    if request.checkpoint.policy != 'from_start':
        event_id = context.start_event_id or request.checkpoint.event_id
        matched = next(
            (index for index, event in enumerate(events) if event.event_id == event_id),
            None,
        )
        if matched is not None:
            start_index = matched
    prefix = events[:start_index] if request.checkpoint.policy != 'from_start' else []
    failed_events = events[start_index:]
    prefix_text = _render_events(prefix, limit=40) or '(none; rerun starts from task input)'
    failure_text = _render_events(failed_events, limit=100) or '(no events recorded)'
    return (
        f'Task goal: {trajectory.goal or "(unknown)"}\n'
        f'Framework: {trajectory.framework or "(unknown)"}\n'
        f'Rerun policy: {request.checkpoint.policy}\n'
        f'Retry directive:\n{request.directive.text}\n\n'
        f'Fixed prefix before the rerun point:\n{prefix_text}\n\n'
        f'Failed execution evidence to learn from, not copy:\n{failure_text}\n\n'
        'Produce the replacement rollout now.'
    )


def trajectory_from_rollout(
    payload: dict[str, Any],
    *,
    source: AgentTrajectory,
    request: RerunRequest,
    parent_event_id: Optional[str] = None,
) -> AgentTrajectory:
    """Normalize a model rollout payload into the portable trajectory schema."""

    raw_events = payload.get('events') or payload.get('continuation_events')
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError('rerun model returned no rollout events')

    trace_id = str(payload.get('trace_id') or f'{source.trace_id}__rerun_{new_id("run")}')
    rerun = AgentTrajectory(
        trace_id=trace_id,
        task_id=source.task_id,
        goal=source.goal,
        framework=source.framework,
        metadata={
            **_rerun_source_metadata(source.metadata),
            'rerun_of': source.trace_id,
            'rerun_report_id': request.report_id,
            'rerun_policy': request.checkpoint.policy,
            'rerun_directive_source': request.directive.source,
            'reported_success': payload.get('success'),
            'rollout_summary': str(payload.get('summary') or ''),
        },
    )
    previous_event_id = parent_event_id
    for ordinal, raw_event in enumerate(raw_events, start=1):
        if not isinstance(raw_event, dict):
            continue
        event_id = str(raw_event.get('event_id') or new_id('evt'))
        rerun.add_event(
            AgentEvent(
                event_id=event_id,
                trace_id=trace_id,
                parent_event_id=(
                    str(raw_event.get('parent_event_id'))
                    if raw_event.get('parent_event_id')
                    else previous_event_id
                ),
                agent_name=str(raw_event.get('agent_name') or 'agent'),
                event_type=_event_type(raw_event.get('event_type')),
                module=str(raw_event.get('module') or '') or None,
                step_index=_step_index(raw_event.get('step_index'), ordinal),
                input=raw_event.get('input'),
                output=raw_event.get('output'),
                error=(
                    str(raw_event.get('error'))
                    if raw_event.get('error') is not None
                    else None
                ),
                metadata={
                    **(
                        dict(raw_event.get('metadata'))
                        if isinstance(raw_event.get('metadata'), dict)
                        else {}
                    ),
                    'rerun_generated': True,
                },
            )
        )
        previous_event_id = event_id
    if not rerun.events:
        raise ValueError('rerun model returned no valid rollout events')
    terminal_events = [
        event for event in rerun.events
        if event.event_type == EventType.RUN_END.value
    ]
    if terminal_events:
        rerun.ended_at = terminal_events[-1].timestamp
    return rerun


def _rerun_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Carry reusable context forward without stale failure expectations."""

    source = dict(metadata or {})
    fixture = source.get('fixture') is True
    cleaned = {
        key: value
        for key, value in source.items()
        if key not in _FAILURE_ONLY_METADATA_KEYS
        and not key.startswith(_FAILURE_ONLY_METADATA_PREFIXES)
    }
    if fixture:
        cleaned.pop('fixture', None)
        cleaned.pop('scenario', None)
    return cleaned


def _render_events(events: list[AgentEvent], *, limit: int) -> str:
    selected = events[-limit:]
    return '\n'.join(
        f'Event {event.event_id} step {event.step_index} [{event.agent_name}] '
        f'{event.event_type}: input={_compact(event.input)} '
        f'output={_compact(event.output)} error={_compact(event.error)}'
        for event in selected
    )


def _compact(value: Any, limit: int = 500) -> str:
    if value is None:
        return 'null'
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return text[:limit]


def _event_type(value: Any) -> EventType:
    try:
        return EventType(str(value or EventType.AGENT_STEP.value))
    except ValueError:
        return EventType.AGENT_STEP


def _step_index(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_openai_base_url(value: str) -> str:
    """Accept either an OpenAI base URL or a chat-completions endpoint."""

    normalized = value.strip().rstrip('/')
    suffix = '/chat/completions'
    return normalized[: -len(suffix)] if normalized.endswith(suffix) else normalized


def _finish_reason(payload: dict[str, Any]) -> Any:
    choices = payload.get('choices') or []
    return choices[0].get('finish_reason') if choices else None


__all__ = [
    'LLMContinuationExecutor',
    'RolloutContext',
    'build_rollout_prompt',
    'normalize_openai_base_url',
    'trajectory_from_rollout',
]
