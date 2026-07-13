"""Second-stage rerun orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agentdebug.rerun.evaluators import LocalProxyEvaluation, evaluate_local_proxy
from agentdebug.rerun.executors.base import RerunExecutor, RerunResult
from agentdebug.rerun.request import RerunCheckpoint, RerunDirective, RerunRequest
from agentdebug.schema import AgentEvent, AgentTrajectory, DiagnosticReport


@dataclass(frozen=True)
class RerunPlan:
    """Auditable plan produced before any rerun execution."""

    request: RerunRequest
    status: str = 'planned'
    execution_required: bool = True
    approval_required: bool = True
    reason: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""

        return asdict(self)


@dataclass(frozen=True)
class RerunWorkflowResult:
    """Output of the Rerun stage."""

    plan: RerunPlan
    execution: Optional[RerunResult] = None
    evaluation: Optional[LocalProxyEvaluation] = None

    @property
    def executed(self) -> bool:
        return self.execution is not None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'stage': 'rerun',
            'status': 'executed' if self.executed else self.plan.status,
            'plan': self.plan.to_dict(),
            'executed': self.executed,
            'evaluation': asdict(self.evaluation) if self.evaluation else None,
        }
        if self.execution is not None:
            payload['execution'] = {
                'trace_id': self.execution.trajectory.trace_id,
                'metadata': self.execution.metadata,
            }
        return payload


class RerunWorkflow:
    """Prepare and optionally execute the paper's second stage.

    The default path only builds a plan. Runtime-specific execution is allowed
    only when the caller supplies an executor and explicitly requests execution.
    """

    def __init__(self, executor: Optional[RerunExecutor] = None) -> None:
        self.executor = executor

    @classmethod
    def suggest_only(cls) -> 'RerunWorkflow':
        """Return a workflow that never executes external systems."""

        return cls(executor=None)

    def plan(
        self,
        report: DiagnosticReport,
        trajectory: Optional[AgentTrajectory] = None,
    ) -> RerunPlan:
        """Build a portable rerun plan from Diagnose output."""

        request = build_rerun_request(report, trajectory)
        return RerunPlan(
            request=request,
            approval_required=request.directive.requires_human_approval,
            reason=(
                'Rerun plan created from diagnostic report. Execution is '
                'separate and requires an approved executor.'
            ),
            metadata={
                'source': 'diagnose',
                'has_trajectory_context': trajectory is not None,
            },
        )

    def run(
        self,
        report: DiagnosticReport,
        trajectory: AgentTrajectory,
        *,
        execute: bool = False,
    ) -> RerunWorkflowResult:
        """Run the second-stage workflow.

        When ``execute`` is false, this returns a plan only. When true, an
        executor must be configured and its resulting trajectory is compared
        against the original trajectory with local proxy evaluation.
        """

        plan = self.plan(report, trajectory)
        if not execute:
            return RerunWorkflowResult(plan=plan)
        if self.executor is None:
            raise ValueError('Rerun execution requires an approved executor.')

        execution = self.executor.run(plan.request)
        evaluation = evaluate_local_proxy(trajectory, execution.trajectory)
        return RerunWorkflowResult(
            plan=plan,
            execution=execution,
            evaluation=evaluation,
        )


def build_rerun_request(
    report: DiagnosticReport,
    trajectory: Optional[AgentTrajectory] = None,
) -> RerunRequest:
    """Construct the portable request consumed by rerun executors."""

    target_event = _target_event(report, trajectory)
    checkpoint = RerunCheckpoint(
        event_id=target_event.event_id if target_event else report.root_cause_event_id,
        step_index=(
            target_event.step_index
            if target_event is not None
            else report.root_cause_step_index
        ),
        policy='from_root_cause',
    )
    directive_text = _directive_text(report)
    directive = RerunDirective(
        text=directive_text,
        source='diagnosis',
        target_event_id=checkpoint.event_id,
        requires_human_approval=_requires_approval(report),
        metadata={
            'summary': report.summary,
            'root_cause_agent': report.root_cause_agent,
        },
    )
    return RerunRequest(
        trace_id=trajectory.trace_id if trajectory is not None else report.trace_id,
        checkpoint=checkpoint,
        directive=directive,
        report_id=report.report_id,
        metadata={
            'task_id': report.task_id,
            'finding_count': len(report.findings),
            'suggestion_count': len(report.suggestions),
        },
    )


def _target_event(
    report: DiagnosticReport,
    trajectory: Optional[AgentTrajectory],
) -> Optional[AgentEvent]:
    if trajectory is None:
        return None
    if report.root_cause_event_id:
        for event in trajectory.events:
            if event.event_id == report.root_cause_event_id:
                return event
    if report.root_cause_step_index is not None:
        for event in trajectory.events:
            if event.step_index == report.root_cause_step_index:
                return event
    for finding in report.findings:
        if finding.event_id:
            for event in trajectory.events:
                if event.event_id == finding.event_id:
                    return event
    return None


def _directive_text(report: DiagnosticReport) -> str:
    recovery = report.recovery or {}
    proposals = recovery.get('proposals') if isinstance(recovery, dict) else None
    if isinstance(proposals, list) and proposals:
        first = proposals[0]
        if isinstance(first, dict):
            suggestion = first.get('suggestion_text')
            if suggestion:
                return str(suggestion)

    if report.suggestions:
        return report.suggestions[0]

    for finding in report.findings:
        if finding.suggestion:
            return finding.suggestion

    return (
        'Retry from the diagnosed root-cause step. Inspect the evidence in the '
        'diagnostic report before execution.'
    )


def _requires_approval(report: DiagnosticReport) -> bool:
    recovery = report.recovery or {}
    proposals = recovery.get('proposals') if isinstance(recovery, dict) else None
    if isinstance(proposals, list) and proposals:
        first = proposals[0]
        if isinstance(first, dict) and 'requires_human_approval' in first:
            return bool(first['requires_human_approval'])
    return True
