"""Runnable HTTP runner example with a real model call and local tool.

This is a protocol demonstration, not a universal framework adapter. Replace
``lookup_policy`` and the model loop with the application's real actor/tools.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

import httpx

from agentdebug.rerun import RerunRequest
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    EventType,
    model_to_dict,
    new_id,
)


def lookup_policy(policy_name: str) -> dict[str, Any]:
    """A real deterministic tool owned by this example environment."""

    policies = {
        'refundable': {'allowed': True, 'deadline_hours': 24},
        'non_refundable': {'allowed': False, 'deadline_hours': 0},
    }
    return policies.get(policy_name, {'allowed': False, 'reason': 'unknown policy'})


def run_agent(
    request: RerunRequest,
    source: AgentTrajectory,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    """Execute one real model/tool rollout for the example policy agent."""

    if cancel_event.is_set():
        raise RuntimeError('cancelled before execution')
    base_url = os.environ['AGENTDEBUG_LIVE_BASE_URL'].rstrip('/')
    api_key = os.environ['AGENTDEBUG_LIVE_API_KEY']
    model = os.environ['AGENTDEBUG_LIVE_MODEL']
    prompt = (
        f'Goal: {source.goal}\n'
        f'Recovery directive: {request.directive.text}\n'
        'Choose exactly one policy tool argument. Return JSON only: '
        '{"policy_name":"refundable|non_refundable","reason":"short reason"}.'
    )
    response = httpx.post(
        f'{base_url}/chat/completions',
        headers={'Authorization': f'Bearer {api_key}'},
        json={
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'response_format': {'type': 'json_object'},
            'temperature': 0,
            'max_tokens': 200,
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()['choices'][0]['message']['content']
    decision = json.loads(content)
    policy_name = str(decision['policy_name'])
    tool_result = lookup_policy(policy_name)

    trace_id = f'{source.trace_id}__live_{new_id("run")}'
    trajectory = AgentTrajectory(
        trace_id=trace_id,
        task_id=source.task_id,
        goal=source.goal,
        framework='agentdebug-http-example',
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.LLM_RESPONSE,
            agent_name='policy_actor',
            output=decision,
            metadata={'model': model, 'observed': True},
        )
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.TOOL_CALL,
            agent_name='policy_actor',
            module='lookup_policy',
            input={'policy_name': policy_name},
            metadata={'observed': True},
        )
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.TOOL_RESULT,
            agent_name='policy_actor',
            module='lookup_policy',
            output=tool_result,
            metadata={'observed': True},
        )
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.RUN_END,
            agent_name='policy_actor',
            output={'success': bool(tool_result.get('allowed'))},
            metadata={'observed': True},
        )
    )
    trajectory.ended_at = trajectory.events[-1].timestamp
    return {
        'execution': {
            'mode': 'live_execution',
            'observed_execution': True,
            'tools_executed': True,
            'tool_execution_count': 1,
            'runner': 'examples.http_agent_runner',
            'framework': trajectory.framework,
        },
        'trajectory': model_to_dict(trajectory),
        'metadata': {'summary': 'model selected and executed lookup_policy'},
    }


__all__ = ['lookup_policy', 'run_agent']
