"""Second-stage rerun orchestration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agentdebug.rerun.evaluators import LocalProxyEvaluation, evaluate_local_proxy
from agentdebug.rerun.executors.base import (
    LIVE_EXECUTION,
    SIMULATED_ROLLOUT,
    RerunExecutor,
    RerunResult,
)
from agentdebug.rerun.request import (
    RerunCapability,
    RerunCheckpoint,
    RerunDirective,
    RerunRequest,
)
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    model_to_json,
)


@dataclass(frozen=True)
class RerunPlan:
    """Auditable plan produced before any rerun execution."""

    request: RerunRequest
    capability: RerunCapability
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
        execution_mode = self.execution.execution_mode if self.execution else None
        simulated = execution_mode == SIMULATED_ROLLOUT
        payload = {
            'stage': 'rerun',
            'status': 'simulated' if simulated else 'executed' if self.executed else self.plan.status,
            'execution_mode': execution_mode,
            'plan': self.plan.to_dict(),
            'executed': self.executed,
            'live_execution': execution_mode == LIVE_EXECUTION,
            'verified': False if simulated else None,
            'evaluation': asdict(self.evaluation) if self.evaluation else None,
        }
        if simulated and payload['evaluation'] is not None:
            payload['evaluation']['scope'] = 'simulated_trajectory_only'
            payload['evaluation']['verified_task_outcome'] = False
        if self.execution is not None:
            payload['execution'] = {
                'trace_id': self.execution.trajectory.trace_id,
                'trajectory': json.loads(model_to_json(self.execution.trajectory)),
                'metadata': self.execution.metadata,
            }
        return payload


class RerunWorkflow:
    """Prepare and optionally execute the paper's second stage.

    The default path only builds a plan. Runtime-specific execution is allowed
    only when the caller supplies an executor and explicitly requests execution.
    """

    def __init__(
        self,
        executor: Optional[RerunExecutor] = None,
        *,
        allow_simulated: bool = False,
    ) -> None:
        self.executor = executor
        self.allow_simulated = allow_simulated

    @classmethod
    def suggest_only(cls) -> 'RerunWorkflow':
        """Return a workflow that never executes external systems."""

        return cls(executor=None)

    def plan(
        self,
        report: DiagnosticReport,
        trajectory: Optional[AgentTrajectory] = None,
        *,
        checkpoint_policy: str = 'from_root_cause',
        checkpoint_event_id: Optional[str] = None,
    ) -> RerunPlan:
        """Build a portable rerun plan from Diagnose output."""

        request = build_rerun_request(
            report,
            trajectory,
            checkpoint_policy=checkpoint_policy,
            checkpoint_event_id=checkpoint_event_id,
        )
        capability = assess_rerun_capability(
            trajectory,
            executor=self.executor,
            allow_simulated=self.allow_simulated,
        )
        return RerunPlan(
            request=request,
            capability=capability,
            status='planned' if capability.executable else 'not_executable',
            execution_required=capability.executable,
            approval_required=request.directive.requires_human_approval,
            reason=capability.reason,
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
        checkpoint_policy: str = 'from_root_cause',
        checkpoint_event_id: Optional[str] = None,
    ) -> RerunWorkflowResult:
        """Run the second-stage workflow.

        When ``execute`` is false, this returns a plan only. When true, an
        executor must be configured and its resulting trajectory is compared
        against the original trajectory with local proxy evaluation.
        """

        plan = self.plan(
            report,
            trajectory,
            checkpoint_policy=checkpoint_policy,
            checkpoint_event_id=checkpoint_event_id,
        )
        if not execute:
            return RerunWorkflowResult(plan=plan)
        if self.executor is None:
            raise ValueError('Rerun execution requires an approved executor.')
        if not plan.capability.executable:
            raise ValueError(plan.capability.reason)

        execution = self.executor.run(plan.request)
        _validate_execution_result(execution, allow_simulated=self.allow_simulated)
        evaluation = evaluate_local_proxy(trajectory, execution.trajectory)
        return RerunWorkflowResult(
            plan=plan,
            execution=execution,
            evaluation=evaluation,
        )


def _validate_execution_result(
    execution: RerunResult,
    *,
    allow_simulated: bool,
) -> None:
    mode = execution.execution_mode
    if mode == LIVE_EXECUTION:
        if execution.metadata.get('observed_execution') is not True:
            raise ValueError(
                'live rerun result must confirm observed_execution=true'
            )
        return
    if mode == SIMULATED_ROLLOUT and allow_simulated:
        return
    if mode == SIMULATED_ROLLOUT:
        raise ValueError(
            'simulated rollout rejected: configure a live executor or explicitly '
            'allow simulation'
        )
    raise ValueError('rerun executor did not declare a valid execution_mode')


def assess_rerun_capability(
    trajectory: Optional[AgentTrajectory],
    *,
    executor: Optional[RerunExecutor] = None,
    allow_simulated: bool = False,
) -> RerunCapability:
    """Classify trajectory-only, replay-bundle, and live-runner inputs."""

    available = ['diagnostic_report']
    if trajectory is not None:
        available.append('trajectory')
    replay = _replay_capabilities(trajectory)
    available.extend(replay)
    mode = str(getattr(executor, 'execution_mode', '') or '') if executor else ''
    if mode == LIVE_EXECUTION:
        return RerunCapability(
            level='live_runner',
            executable=True,
            execution_mode=LIVE_EXECUTION,
            available=tuple(available + ['framework_runner', 'tool_runtime']),
            reason='Live framework runner is configured for real model/tool execution.',
        )
    if mode == SIMULATED_ROLLOUT and allow_simulated:
        return RerunCapability(
            level='trajectory_only',
            executable=True,
            execution_mode=SIMULATED_ROLLOUT,
            missing=('framework_runner', 'tool_runtime', 'environment_state'),
            available=tuple(available),
            reason='Only an explicitly labeled simulation can run; no tools are executed.',
        )
    level = 'replay_bundle' if replay else 'trajectory_only'
    missing = ['framework_runner', 'tool_runtime', 'environment_state']
    if 'environment_snapshot' in replay:
        missing.remove('environment_state')
    if 'tool_manifest' in replay:
        missing.remove('tool_runtime')
    return RerunCapability(
        level=level,
        executable=False,
        missing=tuple(missing),
        available=tuple(available),
        reason=(
            'Real rerun is unavailable from a trajectory alone. Configure a live '
            'framework runner that owns the model, tools, credentials, and environment.'
            if level == 'trajectory_only'
            else 'Replay metadata is present, but a live framework runner is still required.'
        ),
    )


def _replay_capabilities(
    trajectory: Optional[AgentTrajectory],
) -> list[str]:
    if trajectory is None:
        return []
    metadata = trajectory.metadata or {}
    replay = metadata.get('replay_bundle')
    if not isinstance(replay, dict):
        return []
    capabilities = ['replay_bundle']
    if replay.get('environment_snapshot'):
        capabilities.append('environment_snapshot')
    if replay.get('tool_manifest'):
        capabilities.append('tool_manifest')
    if replay.get('dependency_lock'):
        capabilities.append('dependency_lock')
    return capabilities


def build_rerun_request(
    report: DiagnosticReport,
    trajectory: Optional[AgentTrajectory] = None,
    *,
    checkpoint_policy: str = 'from_root_cause',
    checkpoint_event_id: Optional[str] = None,
) -> RerunRequest:
    """Construct the portable request consumed by rerun executors."""

    target_event = None
    if checkpoint_policy != 'from_start':
        if checkpoint_event_id and trajectory is not None:
            target_event = next(
                (
                    event for event in trajectory.events
                    if event.event_id == checkpoint_event_id
                ),
                None,
            )
            if target_event is None:
                raise ValueError(f'unknown rerun checkpoint event: {checkpoint_event_id}')
        else:
            target_event = _target_event(report, trajectory)
    checkpoint = RerunCheckpoint(
        event_id=(
            None
            if checkpoint_policy == 'from_start'
            else target_event.event_id if target_event else report.root_cause_event_id
        ),
        step_index=(
            None
            if checkpoint_policy == 'from_start'
            else target_event.step_index
            if target_event is not None
            else report.root_cause_step_index
        ),
        policy=checkpoint_policy,
    )
    directive_text = _directive_text(report)
    diagnostic_context = _diagnostic_context(report)
    directive = RerunDirective(
        text=directive_text,
        source='diagnosis',
        target_event_id=(
            report.root_cause_event_id
            if checkpoint_policy == 'from_start'
            else checkpoint.event_id
        ),
        requires_human_approval=_requires_approval(report),
        metadata={
            'summary': report.summary,
            'root_cause_agent': report.root_cause_agent,
            'root_cause_event_id': report.root_cause_event_id,
            'root_cause_step_index': report.root_cause_step_index,
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
            'diagnostic_context': diagnostic_context,
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


def _diagnostic_context(report: DiagnosticReport) -> dict[str, Any]:
    attribution = report.attribution if isinstance(report.attribution, dict) else {}
    primary_attribution = attribution.get('primary')
    if not isinstance(primary_attribution, dict):
        hypotheses = attribution.get('hypotheses')
        primary_attribution = (
            hypotheses[0]
            if isinstance(hypotheses, list)
            and hypotheses
            and isinstance(hypotheses[0], dict)
            else None
        )
    recovery = report.recovery if isinstance(report.recovery, dict) else {}
    primary_recovery = recovery.get('primary')
    if not isinstance(primary_recovery, dict):
        proposals = recovery.get('proposals')
        primary_recovery = (
            proposals[0]
            if isinstance(proposals, list)
            and proposals
            and isinstance(proposals[0], dict)
            else None
        )
    return {
        'detect': {
            'summary': report.summary,
            'findings': [_compact_finding(finding) for finding in report.findings[:10]],
        },
        'attribute': _compact_mapping(primary_attribution),
        'recover': _compact_recovery(primary_recovery),
        'root_cause': {
            'event_id': report.root_cause_event_id,
            'agent_name': report.root_cause_agent,
            'step_index': report.root_cause_step_index,
        },
    }


def _compact_finding(finding: Any) -> dict[str, Any]:
    return {
        'failure_mode_id': finding.failure_mode.mode_id,
        'event_id': finding.event_id,
        'agent_name': finding.agent_name,
        'step_index': finding.step_index,
        'evidence': [_truncate(item, 300) for item in finding.evidence[:3]],
        'suggestion': _truncate(finding.suggestion, 500),
    }


def _compact_mapping(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        'event_id': value.get('span_id') or value.get('event_id'),
        'agent_name': value.get('agent_name'),
        'step_index': value.get('step_index'),
        'rationale': _truncate(value.get('rationale'), 1000),
        'evidence': [
            _truncate(item, 300)
            for item in (value.get('evidence') or [])[:3]
        ],
        'sources': [str(item) for item in (value.get('sources') or [])[:5]],
    }


def _compact_recovery(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        'recoverer_id': value.get('recoverer_id'),
        'target_event_id': value.get('target_event_id'),
        'summary': _truncate(value.get('summary'), 500),
        'rationale': _truncate(value.get('rationale'), 1000),
        'suggestion_text': _truncate(value.get('suggestion_text'), 4000),
    }


def _truncate(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + '...'
