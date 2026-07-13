"""OSWorld / CUA trajectory directory import adapter.

Converts a CUA/OSWorld trajectory *directory* (``traj.jsonl``/``trajectory.jsonl``
+ screenshots + ``result.txt``) into a portable ``AgentTrajectory``. It reuses
the vendored CUA loader (pure pydantic, no GUI extra) to parse the on-disk
format, then maps one ``AgentEvent`` per CUA ``Step`` and attaches per-step
screenshots as ``Artifact(modality=IMAGE)`` by URI reference.

This module is the only place allowed to know CUA/OSWorld shapes; downstream
stages consume only the IR. The CUA import is guarded and lazy so that
``import agentdebug`` (and importing this adapter) never requires the vendored
source tree — the import happens inside ``_load_cua_loader`` and any failure is
surfaced as ``ConversionError`` rather than a bare ``ImportError``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    EventType,
    Modality,
    new_id,
)
from agentdebug.ingest.adapters.importers import ConversionError


def _load_cua_loader() -> Tuple[Any, Any]:
    """Import the vendored CUA loader lazily; fail with a clear ConversionError.

    The CUA source tree is imported as the top-level ``debugger`` package, which
    lives under ``<repo>/cua_debugger``. Resolve that directory onto ``sys.path``
    at call time so basic OSWorld ingest works without the ``agentdebugx[gui]``
    extra, and never import ``debugger`` at module top level (Phase 1 core
    import isolation).
    """
    cua_root = Path(__file__).resolve().parents[4] / 'cua_debugger'
    if cua_root.is_dir() and str(cua_root) not in sys.path:
        sys.path.insert(0, str(cua_root))
    try:
        from debugger.ingester import IngestionResult
        from debugger.trajectory import load_trajectory
    except ImportError as exc:
        raise ConversionError(
            'OSWorld ingest requires the vendored CUA source tree (cua_debugger) '
            'on sys.path. Add cua_debugger/ to your PYTHONPATH.'
        ) from exc
    return IngestionResult, load_trajectory


def convert_osworld_dir(
    path,
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Load an OSWorld trajectory directory and normalize it into ``AgentTrajectory``."""

    in_path = Path(path)
    if not in_path.exists():
        raise ConversionError(f'input directory does not exist: {in_path}')
    if not in_path.is_dir():
        raise ConversionError(f'osworld ingest expects a directory: {in_path}')

    IngestionResult, load_trajectory = _load_cua_loader()
    try:
        result = IngestionResult.from_directory(in_path)
        raw = load_trajectory(in_path)
    except FileNotFoundError as exc:
        raise ConversionError(str(exc)) from exc

    return _convert_osworld(
        result,
        raw,
        trace_id=trace_id,
        task_id=task_id,
        goal=goal,
        framework=framework,
        source_dir=in_path,
    )


def _convert_osworld(
    result: Any,
    raw: Dict[str, Any],
    *,
    trace_id: Optional[str],
    task_id: Optional[str],
    goal: Optional[str],
    framework: Optional[str],
    source_dir: Optional[Any] = None,
) -> AgentTrajectory:
    resolved_task_id = task_id or (result.task_id or None)
    resolved_trace_id = trace_id or (
        f'osworld_{resolved_task_id}' if resolved_task_id else None
    )
    traj = AgentTrajectory(
        trace_id=resolved_trace_id or new_id('trace'),
        task_id=resolved_task_id,
        goal=goal or (result.instruction or None),
        framework=framework or 'osworld',
        metadata={
            'task_id': result.task_id,
            'is_infeasible': result.is_infeasible,
            'result_score': raw.get('result_score'),
            'status': result.status,
            'terminal_step': result.terminal_step,
            'evaluator_func': raw.get('evaluator_func') or '',
            'format': result.fmt,
            'source_format': 'osworld',
            # Absolute trajectory directory so GUI RCA can reconstruct CUA's
            # IngestionResult + on-disk screenshot root (osworld_root).
            'source_dir': (
                str(Path(source_dir).resolve()) if source_dir is not None else None
            ),
        },
    )
    for step in result.trajectory:
        event = AgentEvent(
            trace_id=traj.trace_id,
            agent_name='agent',
            event_type=EventType.AGENT_STEP,
            module='gui',
            step_index=step.step_num,
            input=step.reasoning or None,
            output=step.action_code or None,
            error=(step.error.strip() or None) if step.error else None,
            metadata={
                'source_format': 'osworld',
                'reward': step.reward,
                'done': step.done,
                'action_type': step.action_type,
                'llm_tool_use': step.llm_tool_use,
            },
        )
        if step.screenshot_path is not None:
            spath = Path(step.screenshot_path)
            suffix = spath.suffix.lower()
            media_type = (
                'image/png'
                if suffix in ('', '.png')
                else f'image/{suffix.lstrip(".")}'
            )
            event.artifacts.append(
                Artifact(
                    uri=str(spath),
                    modality=Modality.IMAGE,
                    media_type=media_type,
                    description=f'screenshot for step {step.step_num}',
                )
            )
        traj.add_event(event)
    return traj
