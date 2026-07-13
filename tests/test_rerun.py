from __future__ import annotations

import pytest

from agentdebug.rerun import RerunResult, RerunWorkflow, build_rerun_request
from agentdebug.rerun.branch import compare_branches
from agentdebug.rerun.evaluators import evaluate_local_proxy
from agentdebug.schema import AgentEvent, AgentTrajectory, DiagnosticReport, EventType


def _trajectory(trace_id: str, error_count: int) -> AgentTrajectory:
    trajectory = AgentTrajectory(trace_id=trace_id)
    for step in range(error_count):
        trajectory.add_event(
            AgentEvent(
                trace_id=trace_id,
                event_type=EventType.ERROR,
                step_index=step,
                error=f'error-{step}',
            )
        )
    return trajectory


@pytest.mark.parametrize(
    ('before', 'after', 'expected'),
    [(2, 1, 'improved'), (1, 1, 'unchanged'), (1, 2, 'worse')],
)
def test_branch_comparison_results(before: int, after: int, expected: str) -> None:
    comparison = compare_branches(
        _trajectory('before', before),
        _trajectory('after', after),
    )

    assert comparison.result == expected
    assert comparison.original_error_count == before
    assert comparison.rerun_error_count == after


def test_local_proxy_evaluation_scores_clean_rerun() -> None:
    evaluation = evaluate_local_proxy(
        _trajectory('before', 1),
        _trajectory('after', 0),
    )

    assert evaluation.result == 'improved'
    assert evaluation.score_before == 0
    assert evaluation.score_after == 1


def test_build_request_prefers_recovery_proposal(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.recovery = {
        'proposals': [
            {
                'suggestion_text': 'Use the approved recovery directive.',
                'requires_human_approval': False,
            }
        ]
    }

    request = build_rerun_request(diagnostic_report, failed_trajectory)

    assert request.checkpoint.event_id == 'evt_plan'
    assert request.directive.text == 'Use the approved recovery directive.'
    assert request.directive.requires_human_approval is False


def test_suggest_only_workflow_never_executes(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    result = RerunWorkflow.suggest_only().run(
        diagnostic_report,
        failed_trajectory,
    )

    assert result.executed is False
    assert result.plan.status == 'planned'
    assert result.evaluation is None


def test_execution_requires_an_executor(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    with pytest.raises(ValueError, match='approved executor'):
        RerunWorkflow.suggest_only().run(
            diagnostic_report,
            failed_trajectory,
            execute=True,
        )


def test_approved_executor_is_evaluated(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class Executor:
        id = 'test-executor'

        def run(self, request):
            rerun = AgentTrajectory(trace_id='trace-rerun')
            rerun.add_event(
                AgentEvent(
                    trace_id=rerun.trace_id,
                    event_type=EventType.TOOL_RESULT,
                    output={'ok': True},
                )
            )
            return RerunResult(
                request=request,
                trajectory=rerun,
                metadata={'executor': self.id},
            )

    result = RerunWorkflow(Executor()).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
    )

    assert result.executed is True
    assert result.evaluation is not None
    assert result.evaluation.result == 'improved'
    assert result.to_dict()['execution']['trace_id'] == 'trace-rerun'


def test_request_without_suggestion_uses_safe_default() -> None:
    report = DiagnosticReport(trace_id='trace-empty', summary='No suggestion')

    request = build_rerun_request(report)

    assert request.trace_id == 'trace-empty'
    assert 'Inspect the evidence' in request.directive.text
    assert request.directive.requires_human_approval is True
