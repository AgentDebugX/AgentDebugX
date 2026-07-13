"""Rerun evaluation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from agentdebug.schema import AgentTrajectory
from agentdebug.rerun.branch import compare_branches


@dataclass(frozen=True)
class LocalProxyEvaluation:
    """Local, task-agnostic proxy evaluation for a rerun branch."""

    result: str
    score_before: int
    score_after: int
    error_count_before: int
    error_count_after: int
    method: str = 'local_proxy'


def evaluate_local_proxy(
    original: AgentTrajectory,
    rerun: AgentTrajectory,
) -> LocalProxyEvaluation:
    """Score a rerun branch by whether local error signals were reduced."""

    comparison = compare_branches(original, rerun)
    return LocalProxyEvaluation(
        result=comparison.result,
        score_before=1 if comparison.original_error_count == 0 else 0,
        score_after=1 if comparison.rerun_error_count == 0 else 0,
        error_count_before=comparison.original_error_count,
        error_count_after=comparison.rerun_error_count,
    )
