"""Base protocol for approved rerun executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentdebug.schema import AgentTrajectory
from agentdebug.rerun.request import RerunRequest

LIVE_EXECUTION = 'live_execution'
SIMULATED_ROLLOUT = 'simulated_rollout'


@dataclass(frozen=True)
class RerunResult:
    """Output from a runtime-specific rerun executor."""

    request: RerunRequest
    trajectory: AgentTrajectory
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def execution_mode(self) -> str:
        return str(self.metadata.get('execution_mode') or '')


class RerunExecutor(Protocol):
    """Protocol implemented by runtime-specific rerun backends."""

    id: str
    execution_mode: str

    def run(self, request: RerunRequest) -> RerunResult:
        ...


__all__ = [
    'LIVE_EXECUTION',
    'SIMULATED_ROLLOUT',
    'RerunExecutor',
    'RerunResult',
]
