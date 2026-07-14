"""Validation shared by live rerun transports."""

from __future__ import annotations

import json
from typing import Any

from agentdebug.rerun.executors.base import LIVE_EXECUTION
from agentdebug.schema import AgentTrajectory, EventType, trajectory_from_json


def parse_live_result(
    payload: Any,
) -> tuple[AgentTrajectory, dict[str, Any], dict[str, Any]]:
    """Validate live execution proof and return its observed trajectory."""

    if not isinstance(payload, dict):
        raise ValueError('live rerun output must be a JSON object')
    proof = payload.get('execution')
    if not isinstance(proof, dict):
        raise ValueError('live rerun output requires an execution proof object')
    if proof.get('mode') != LIVE_EXECUTION:
        raise ValueError('live rerun execution.mode must be live_execution')
    if proof.get('observed_execution') is not True:
        raise ValueError('live rerun execution.observed_execution must be true')
    tools_executed = proof.get('tools_executed')
    if not isinstance(tools_executed, bool):
        raise ValueError('live rerun execution.tools_executed must be a boolean')
    runner = str(proof.get('runner') or '').strip()
    if not runner:
        raise ValueError('live rerun execution.runner is required')
    framework = str(proof.get('framework') or '').strip()
    if not framework:
        raise ValueError('live rerun execution.framework is required')
    tool_execution_count = proof.get('tool_execution_count')
    if not isinstance(tool_execution_count, int) or tool_execution_count < 0:
        raise ValueError(
            'live rerun execution.tool_execution_count must be a non-negative integer'
        )
    if tools_executed != (tool_execution_count > 0):
        raise ValueError(
            'live rerun tools_executed must match tool_execution_count'
        )
    trajectory_payload = payload.get('trajectory')
    if not isinstance(trajectory_payload, dict):
        raise ValueError('live rerun output requires a trajectory object')
    trajectory = trajectory_from_json(json.dumps(trajectory_payload))
    if not trajectory.events:
        raise ValueError('live rerun trajectory must contain observed events')
    observed_tool_calls = sum(
        1
        for event in trajectory.events
        if str(getattr(event.event_type, 'value', event.event_type))
        == EventType.TOOL_CALL.value
    )
    if observed_tool_calls < tool_execution_count:
        raise ValueError(
            'live rerun trajectory does not contain the declared tool executions'
        )
    metadata = payload.get('metadata')
    return trajectory, proof, dict(metadata) if isinstance(metadata, dict) else {}


__all__ = ['parse_live_result']
