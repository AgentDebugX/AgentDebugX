"""Shared types for analysis rule packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from agentdebug.core.models import AgentEvent, AgentTrajectory, FailureFinding


@dataclass(frozen=True)
class RuleMatch:
    """A rule hit before it is materialized as ``FailureFinding``."""

    mode_id: str
    evidence: List[str]
    rule_id: str
    rule_pack: str
    confidence_basis: str
    priority: int = 100
    metadata: Dict[str, object] = field(default_factory=dict)
    confidence: Optional[float] = None


class EventRule(Protocol):
    """Protocol implemented by single-event deterministic rules."""

    rule_id: str
    rule_pack: str
    priority: int

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        ...


class TrajectoryRule(Protocol):
    """Protocol implemented by cross-event deterministic rules."""

    rule_id: str
    rule_pack: str
    priority: int

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        ...
