"""Live rerun executor backed by a trusted framework runner process."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from agentdebug.rerun.executors.base import LIVE_EXECUTION, RerunResult
from agentdebug.rerun.request import RerunRequest
from agentdebug.rerun.live_validation import parse_live_result
from agentdebug.schema import AgentTrajectory, model_to_json


class ProcessLiveExecutor:
    """Run an application-owned agent runner with its real model and tools.

    The command receives file locations through environment variables and must
    write a JSON envelope to ``AGENTDEBUG_RERUN_OUTPUT``. AgentDebugX trusts the
    configured process as the framework boundary but validates its execution
    proof and normalized trajectory before accepting the result.
    """

    id = 'process_live'
    execution_mode = LIVE_EXECUTION

    def __init__(
        self,
        command: Union[str, Sequence[str]],
        source: AgentTrajectory,
        *,
        cwd: Optional[Union[str, Path]] = None,
        timeout: float = 1800.0,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        parsed = shlex.split(command) if isinstance(command, str) else list(command)
        if not parsed:
            raise ValueError('live rerun command cannot be empty')
        self.command = parsed
        self.source = source
        self.cwd = str(Path(cwd).expanduser()) if cwd is not None else None
        self.timeout = timeout
        self.env = dict(env or {})

    def run(self, request: RerunRequest) -> RerunResult:
        with tempfile.TemporaryDirectory(prefix='agentdebug-rerun-') as temp_dir:
            workspace = Path(temp_dir)
            request_path = workspace / 'request.json'
            source_path = workspace / 'source.trajectory.json'
            output_path = workspace / 'result.json'
            request_path.write_text(
                json.dumps(asdict(request), indent=2),
                encoding='utf-8',
            )
            source_path.write_text(
                model_to_json(self.source, indent=2),
                encoding='utf-8',
            )
            process_env = {
                **os.environ,
                **self.env,
                'AGENTDEBUG_RERUN_REQUEST': str(request_path),
                'AGENTDEBUG_RERUN_SOURCE': str(source_path),
                'AGENTDEBUG_RERUN_OUTPUT': str(output_path),
            }
            completed = subprocess.run(
                self.command,
                cwd=self.cwd,
                env=process_env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip()[-2000:]
                raise RuntimeError(
                    f'live rerun runner exited with {completed.returncode}: {stderr}'
                )
            if not output_path.exists():
                raise RuntimeError(
                    'live rerun runner did not write AGENTDEBUG_RERUN_OUTPUT'
                )
            try:
                payload = json.loads(output_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as exc:
                raise ValueError('live rerun output is not valid JSON') from exc

        trajectory, proof, metadata = parse_live_result(payload)
        trajectory.metadata.update(
            {
                'rerun_of': self.source.trace_id,
                'rerun_report_id': request.report_id,
                'rerun_policy': request.checkpoint.policy,
                'execution_mode': LIVE_EXECUTION,
                'observed_execution': True,
                'tools_executed': proof['tools_executed'],
                'live_runner': proof['runner'],
            }
        )
        return RerunResult(
            request=request,
            trajectory=trajectory,
            metadata={
                **metadata,
                'executor': self.id,
                'execution_mode': LIVE_EXECUTION,
                'observed_execution': True,
                'tools_executed': proof['tools_executed'],
                'runner': proof['runner'],
                'framework': proof.get('framework'),
                'tool_execution_count': proof['tool_execution_count'],
                'command': self.command[0],
            },
        )

__all__ = ['ProcessLiveExecutor']
