"""Base protocol for approved rerun executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentdebug.schema import AgentTrajectory
from agentdebug.rerun.request import RerunRequest


@dataclass(frozen=True)
class RerunResult:
    """Output from a runtime-specific rerun executor."""

    request: RerunRequest
    trajectory: AgentTrajectory
    metadata: dict[str, Any] = field(default_factory=dict)


class RerunExecutor(Protocol):
    """Protocol implemented by runtime-specific rerun backends."""

    id: str

    def run(self, request: RerunRequest) -> RerunResult:
        ...
