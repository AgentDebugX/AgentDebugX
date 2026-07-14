"""Portable pending tasks for application-owned actor rollouts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Union

from agentdebug.rerun.request import RerunRequest
from agentdebug.schema import AgentTrajectory, DiagnosticReport, model_to_dict

ACTOR_TASK_SCHEMA_VERSION = '1.0'
ACTOR_TASK_RECORD_TYPE = 'agentdebug.rerun.actor_task'

_ACTOR_SYSTEM_MESSAGE = (
    'Execute the original agent task in its real runtime using the available '
    'model, tools, credentials, and environment. Apply the approved recovery '
    'directive. Record observable execution as an AgentTrajectory; do not '
    'invent tool results.'
)


@dataclass(frozen=True)
class ActorRerunTask:
    """One pending rerun task suitable for JSONL or Parquet datasets."""

    schema_version: str
    record_type: str
    task_id: str
    trace_id: str
    report_id: Optional[str]
    source_task_id: Optional[str]
    goal: str
    framework: Optional[str]
    messages: list[dict[str, str]]
    retry_directive: str
    checkpoint_policy: str
    checkpoint_event_id: Optional[str]
    checkpoint_step_index: Optional[int]
    rerun_request_json: str
    diagnostic_context_json: str
    source_trajectory_json: str
    expected_output_schema_json: str
    required_capabilities_json: str
    status: str = 'pending'
    requires_live_environment: bool = True
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_actor_rerun_task(
    request: RerunRequest,
    report: DiagnosticReport,
    trajectory: AgentTrajectory,
) -> ActorRerunTask:
    """Build a framework-neutral task for a user's actor runtime."""

    diagnostic_context = request.metadata.get('diagnostic_context') or {}
    actor_input = {
        'goal': trajectory.goal or '',
        'retry_directive': request.directive.text,
        'checkpoint': asdict(request.checkpoint),
        'diagnostic_context': diagnostic_context,
    }
    user_message = (
        'Run this task again and return a newly observed AgentTrajectory.\n\n'
        + json.dumps(actor_input, ensure_ascii=False, indent=2)
    )
    task_id = f'rerun_task_{request.report_id or request.trace_id}'
    return ActorRerunTask(
        schema_version=ACTOR_TASK_SCHEMA_VERSION,
        record_type=ACTOR_TASK_RECORD_TYPE,
        task_id=task_id,
        trace_id=request.trace_id,
        report_id=request.report_id,
        source_task_id=trajectory.task_id or report.task_id,
        goal=trajectory.goal or '',
        framework=trajectory.framework,
        messages=[
            {'role': 'system', 'content': _ACTOR_SYSTEM_MESSAGE},
            {'role': 'user', 'content': user_message},
        ],
        retry_directive=request.directive.text,
        checkpoint_policy=request.checkpoint.policy,
        checkpoint_event_id=request.checkpoint.event_id,
        checkpoint_step_index=request.checkpoint.step_index,
        rerun_request_json=json.dumps(asdict(request), ensure_ascii=False),
        diagnostic_context_json=json.dumps(diagnostic_context, ensure_ascii=False),
        source_trajectory_json=json.dumps(
            model_to_dict(trajectory), ensure_ascii=False
        ),
        expected_output_schema_json=json.dumps(
            {
                'type': 'AgentTrajectory',
                'execution_mode': 'live_execution',
                'required_event_sources': 'observed',
            }
        ),
        required_capabilities_json=json.dumps(
            ['framework_runner', 'tool_runtime', 'environment_state']
        ),
    )


def export_actor_rerun_tasks(
    tasks: Iterable[ActorRerunTask],
    path: Union[str, Path],
    *,
    format: str,
) -> Path:
    """Write pending actor tasks as JSONL or optional Parquet."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = [task.to_dict() for task in tasks]
    if not records:
        raise ValueError('actor task export requires at least one task')
    if format == 'jsonl':
        destination.write_text(
            ''.join(
                json.dumps(record, ensure_ascii=False) + '\n'
                for record in records
            ),
            encoding='utf-8',
        )
        return destination
    if format == 'parquet':
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                'Parquet export requires `pip install pyarrow`.'
            ) from exc
        table = pa.Table.from_pylist(records)
        pq.write_table(table, destination)
        return destination
    raise ValueError('actor task format must be jsonl or parquet')


__all__ = [
    'ACTOR_TASK_RECORD_TYPE',
    'ACTOR_TASK_SCHEMA_VERSION',
    'ActorRerunTask',
    'build_actor_rerun_task',
    'export_actor_rerun_tasks',
]
