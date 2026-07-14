"""Diagnose-stage orchestration.

The paper describes diagnosis as three ordered sub-stages:

1. Detect observable failures.
2. Attribute those symptoms to a responsible step/agent.
3. Recover by turning the localized diagnosis into retry guidance.

This module provides that shape without replacing the existing analyzer,
attributor, and recoverer implementations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Protocol

from agentdebug.schema import AgentTrajectory, DiagnosticReport
from agentdebug.diagnose.context import DiagnoseContext
from agentdebug.diagnose.detect import HeuristicAnalyzer
from agentdebug.diagnose.attribute import (
    AttributionResult,
    Attributor,
    HeuristicAttributor,
)
from agentdebug.diagnose.recover import FixProposal, Recoverer, suggest_from_context


class DetectorStage(Protocol):
    """Protocol for components that produce a diagnostic report."""

    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        ...


@dataclass
class DiagnosePipelineResult:
    """Structured output from the Diagnose stage."""

    report: DiagnosticReport
    attribution: Optional[AttributionResult] = None
    recovery: Optional[list[FixProposal]] = None
    context: Optional[DiagnoseContext] = None


class DiagnosePipeline:
    """Run Detect -> Attribute -> Recover over one trajectory.

    Defaults are intentionally local and dependency-free. Passing ``None`` for
    ``attributor`` or ``recoverer`` disables that sub-stage.
    """

    def __init__(
        self,
        *,
        detector: Optional[DetectorStage] = None,
        attributor: Optional[Attributor] = None,
        recoverer: Optional[Recoverer] = None,
    ) -> None:
        self.detector = detector or HeuristicAnalyzer()
        self.attributor = attributor
        self.recoverer = recoverer

    @classmethod
    def local_default(cls) -> 'DiagnosePipeline':
        """Return a zero-LLM pipeline with deterministic detect + attribution."""

        return cls(detector=HeuristicAnalyzer(), attributor=HeuristicAttributor())

    def run(self, trajectory: AgentTrajectory) -> DiagnosePipelineResult:
        report = self.detector.analyze(trajectory)
        attribution = None
        recovery = None

        if self.attributor is not None:
            attribution = self.attributor.attribute(trajectory, report.findings)
            report.attribution = _attribution_payload(attribution)

        context = DiagnoseContext.build(trajectory, report, attribution)

        if self.recoverer is not None:
            recovery = suggest_from_context(self.recoverer, context)
            report.recovery = _recovery_payload(recovery)
            if recovery:
                report.suggestions = [proposal.suggestion_text for proposal in recovery]

        return DiagnosePipelineResult(
            report=report,
            context=context,
            attribution=attribution,
            recovery=recovery,
        )


def _attribution_payload(result: AttributionResult) -> dict[str, object]:
    hypotheses = [asdict(hypothesis) for hypothesis in result.hypotheses]
    return {
        'method': result.method,
        'elapsed_ms': result.elapsed_ms,
        'hypotheses': hypotheses,
        'primary': hypotheses[0] if hypotheses else None,
        'raw': result.raw,
    }


def _recovery_payload(proposals: list[FixProposal]) -> dict[str, object]:
    return {
        'proposal_count': len(proposals),
        'proposals': [asdict(proposal) for proposal in proposals],
    }
