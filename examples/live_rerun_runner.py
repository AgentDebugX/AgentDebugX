"""Template for connecting an application-owned agent runtime to AgentDebugX."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def run_agent_with_real_tools(
    request: dict[str, Any],
    source: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Replace this function with the project's real framework invocation."""

    raise NotImplementedError(
        'Load your agent, model, tools, credentials, and environment here; '
        'return the observed AgentTrajectory payload and tool execution count.'
    )


def main() -> None:
    request_path = Path(os.environ['AGENTDEBUG_RERUN_REQUEST'])
    source_path = Path(os.environ['AGENTDEBUG_RERUN_SOURCE'])
    output_path = Path(os.environ['AGENTDEBUG_RERUN_OUTPUT'])
    request = json.loads(request_path.read_text(encoding='utf-8'))
    source = json.loads(source_path.read_text(encoding='utf-8'))

    trajectory, tool_execution_count = run_agent_with_real_tools(request, source)
    payload = {
        'execution': {
            'mode': 'live_execution',
            'observed_execution': True,
            'tools_executed': tool_execution_count > 0,
            'tool_execution_count': tool_execution_count,
            'runner': 'replace_with_project.runner',
            'framework': trajectory.get('framework') or 'replace_with_framework',
        },
        'trajectory': trajectory,
        'metadata': {'summary': 'live framework rerun completed'},
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
