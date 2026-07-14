"""OpenAI-compatible model executor for trajectory reruns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from agentdebug.rerun.executors.base import RerunResult, SIMULATED_ROLLOUT
from agentdebug.rerun.request import RerunRequest
from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType, new_id

_ROLLOUT_PROMPT = (
    'You are simulating how an agent task might proceed after a failed attempt '
    'was diagnosed. You cannot execute tools or observe the real environment. '
    'Generate one plausible corrected trajectory using the retry directive; '
    'never imply that its tool outputs or outcome were actually observed.\n'
    'Return JSON only: {"summary": "<hypothetical outcome>", "success": <bool>, '
    '"events": [{"agent_name": "<agent>", "event_type": "<event type>", '
    '"module": "<module>", "step_index": <int>, "input": <value>, '
    '"output": <value>, "error": <string or null>, "metadata": {}}]}. '
    'Events must be chronological and contain enough detail to inspect the '
    'hypothesis. The success field is only your prediction. Never include '
    'hidden chain-of-thought.'
    '\nAllowed event_type values: run.start, run.end, agent.step, llm.call, '
    'llm.response, tool.call, tool.result, memory.read, memory.write, '
    'reflection, plan, handoff, guardrail, observation, error, human.feedback.'
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


class SimulatedRerunExecutor:
    """Simulate a trajectory with an OpenAI-compatible LLM client.

    This executor does not invoke the original agent tools or environment. Its
    output is always labeled ``simulated_rollout`` and is rejected by the
    default live Rerun workflow unless simulation is explicitly allowed.
    """

    id = 'simulated_rerun'
    execution_mode = SIMULATED_ROLLOUT

    def __init__(
        self,
        llm: LLMClient,
        context: RolloutContext,
        *,
        max_tokens: int = 8192,
        max_attempts: int = 2,
    ) -> None:
        self.llm = llm
        self.context = context
        self.max_tokens = max_tokens
        self.max_attempts = max(1, max_attempts)
        self._supports_response_format = True

    def run(self, request: RerunRequest) -> RerunResult:
        prompt = self.context.prompt_override or build_rollout_prompt(
            request,
            self.context,
        )
        payload, completion, attempts, token_budget = self._generate(prompt)
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
                'execution_mode': SIMULATED_ROLLOUT,
                'tools_executed': False,
                'artifact_type': 'hypothetical_trajectory',
                'verified': False,
                'model': self.llm.model,
                'checkpoint_policy': request.checkpoint.policy,
                'source_trace_id': self.context.trajectory.trace_id,
                'response_summary': str(payload.get('summary') or ''),
                'model_claimed_success': payload.get('success'),
                'usage': completion.raw.get('usage'),
                'finish_reason': _finish_reason(completion.raw),
                'generation_attempts': attempts,
                'final_token_budget': token_budget,
                'output_validated': True,
                'disclaimer': (
                    'Model-generated simulation; no agent tools or external '
                    'environment were executed.'
                ),
            },
        )

    def _generate(self, prompt: str) -> tuple[dict[str, Any], Any, int, int]:
        errors: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            token_budget = min(self.max_tokens * (2 ** (attempt - 1)), 16384)
            retry_note = (
                '\n\nThe previous response was truncated or invalid. Return '
                'a complete JSON object from the beginning.'
                if attempt > 1
                else ''
            )
            kwargs: dict[str, Any] = {
                'temperature': 0.2,
                'max_tokens': token_budget,
            }
            if self._supports_response_format:
                kwargs['response_format'] = {'type': 'json_object'}
            try:
                completion = self.llm.complete(
                    messages=[
                        {'role': 'system', 'content': _ROLLOUT_PROMPT},
                        {'role': 'user', 'content': prompt + retry_note},
                    ],
                    **kwargs,
                )
            except Exception as exc:
                if _response_format_unsupported(exc):
                    self._supports_response_format = False
                errors.append(f'attempt {attempt}: model call failed: {exc}')
                continue
            finish_reason = _finish_reason(completion.raw)
            if _is_length_limited(finish_reason):
                errors.append(f'attempt {attempt} was truncated')
                continue
            payload = extract_json_block(completion.text)
            error = _simulation_payload_error(payload)
            if error is None:
                return payload, completion, attempt, token_budget
            errors.append(f'attempt {attempt}: {error}')
        raise ValueError(
            'simulation model did not return a valid trajectory: ' + '; '.join(errors)
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
    diagnostic_context = request.metadata.get('diagnostic_context')
    diagnostic_text = (
        json.dumps(diagnostic_context, ensure_ascii=False, indent=2)
        if isinstance(diagnostic_context, dict)
        else '(no structured diagnosis available)'
    )
    return (
        f'Task goal: {trajectory.goal or "(unknown)"}\n'
        f'Framework: {trajectory.framework or "(unknown)"}\n'
        f'Rerun policy: {request.checkpoint.policy}\n'
        f'Diagnostic context (Detect -> Attribute -> Recover):\n'
        f'{diagnostic_text}\n\n'
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
            'model_claimed_success': payload.get('success'),
            'execution_mode': SIMULATED_ROLLOUT,
            'tools_executed': False,
            'artifact_type': 'hypothetical_trajectory',
            'verified': False,
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
                    'simulated': True,
                    'observation_source': 'model_generated',
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
    choice = choices[0] if choices else {}
    return (
        choice.get('finish_reason')
        or choice.get('stop_reason')
        or payload.get('finish_reason')
        or payload.get('stop_reason')
    )


def _is_length_limited(reason: Any) -> bool:
    normalized = str(reason or '').strip().lower()
    return normalized == 'length' or 'token' in normalized


def _simulation_payload_error(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return 'response is not a JSON object'
    if not isinstance(payload.get('summary'), str) or not payload['summary'].strip():
        return 'summary must be a non-empty string'
    if not isinstance(payload.get('success'), bool):
        return 'success must be a boolean'
    events = payload.get('events') or payload.get('continuation_events')
    if not isinstance(events, list) or not events:
        return 'events must be a non-empty array'
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return f'events[{index}] must be an object'
        if not str(event.get('event_type') or '').strip():
            return f'events[{index}].event_type is required'
        try:
            EventType(str(event['event_type']))
        except ValueError:
            return f'events[{index}].event_type is not supported'
        if not any(key in event for key in ('input', 'output', 'error')):
            return f'events[{index}] requires input, output, or error'
    return None


def _response_format_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return 'response_format' in message and any(
        marker in message
        for marker in ('unsupported', 'not support', 'unknown', 'invalid')
    )


LLMContinuationExecutor = SimulatedRerunExecutor


__all__ = [
    'LLMContinuationExecutor',
    'SimulatedRerunExecutor',
    'RolloutContext',
    'build_rollout_prompt',
    'normalize_openai_base_url',
    'trajectory_from_rollout',
]
