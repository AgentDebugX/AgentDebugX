"""Rerun branch comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agentdebug.schema import AgentTrajectory


@dataclass(frozen=True)
class RerunBranch:
    """A trajectory branch produced from an original failed run."""

    branch_id: str
    trajectory: AgentTrajectory
    parent_trace_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerunComparison:
    """Small, source-independent comparison between original and rerun branch."""

    original_trace_id: str
    rerun_trace_id: str
    original_error_count: int
    rerun_error_count: int
    result: str


def compare_branches(
    original: AgentTrajectory,
    rerun: AgentTrajectory,
) -> RerunComparison:
    """Compare two trajectories using local error counts only."""

    original_errors = _error_count(original)
    rerun_errors = _error_count(rerun)
    if rerun_errors < original_errors:
        result = 'improved'
    elif rerun_errors > original_errors:
        result = 'worse'
    else:
        result = 'unchanged'
    return RerunComparison(
        original_trace_id=original.trace_id,
        rerun_trace_id=rerun.trace_id,
        original_error_count=original_errors,
        rerun_error_count=rerun_errors,
        result=result,
    )


def _error_count(trajectory: AgentTrajectory) -> int:
    return sum(1 for event in trajectory.events if event.error)
