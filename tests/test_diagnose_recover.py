from __future__ import annotations

import pytest

from agentdebug.diagnose.attribute import AttributionResult, Blame
from agentdebug.diagnose.context import DiagnoseContext
from agentdebug.diagnose.pipeline import DiagnosePipeline
from agentdebug.diagnose.recover import (
    AutoManualRules,
    CriticRecoverer,
    ReflexionSuggestion,
    SelfRefineLoop,
    VerifierSpec,
)
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentTrajectory, DiagnosticReport


def test_diagnose_context_uses_primary_attribution_as_recovery_target(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.findings[0].event_id = 'evt_tool'
    diagnostic_report.findings[0].agent_name = 'browser'
    diagnostic_report.findings[0].step_index = 2
    attribution = AttributionResult(
        method='test',
        hypotheses=[
            Blame(
                span_id='evt_plan',
                step_index=1,
                agent_name='planner',
                confidence=0.9,
                rationale='The plan dropped the required constraint.',
                evidence=['Search for the cheapest flight.'],
                sources=['test_attributor'],
            )
        ],
    )

    context = DiagnoseContext.build(
        failed_trajectory,
        diagnostic_report,
        attribution,
    )

    assert context.recovery_target is not None
    assert context.recovery_target.event_id == 'evt_plan'
    assert context.recovery_report.root_cause_event_id == 'evt_plan'
    assert context.recovery_report.findings == [context.recovery_target]
    diagnose_context = context.recovery_report.metadata['diagnose_context']
    assert diagnose_context['detect']['findings'][0]['event_id'] == 'evt_tool'
    assert diagnose_context['attribute']['span_id'] == 'evt_plan'
    assert diagnose_context['recovery_target']['event_id'] == 'evt_plan'
    assert diagnostic_report.root_cause_event_id == 'evt_plan'
    assert diagnostic_report.metadata['recovery_target']['source'] == (
        'primary_attribution'
    )


def test_pipeline_passes_attribution_target_to_recoverer(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.findings[0].event_id = 'evt_tool'
    diagnostic_report.findings[0].agent_name = 'browser'
    diagnostic_report.findings[0].step_index = 2

    class Detector:
        def analyze(self, trajectory):
            return diagnostic_report

    class Attributor:
        def attribute(self, trajectory, findings):
            return AttributionResult(
                method='test',
                hypotheses=[
                    Blame(
                        span_id='evt_plan',
                        step_index=1,
                        agent_name='planner',
                        confidence=0.9,
                        rationale='The plan caused the later tool failure.',
                        evidence=['Search for the cheapest flight.'],
                    )
                ],
            )

    result = DiagnosePipeline(
        detector=Detector(),
        attributor=Attributor(),
        recoverer=ReflexionSuggestion(),
    ).run(failed_trajectory)

    assert result.recovery is not None
    assert len(result.recovery) == 1
    assert result.recovery[0].target_event_id == 'evt_plan'
    assert result.report.root_cause_event_id == 'evt_plan'


def test_attribution_can_create_recovery_target_without_detector_findings(
    failed_trajectory: AgentTrajectory,
) -> None:
    report = DiagnosticReport(trace_id=failed_trajectory.trace_id)
    attribution = AttributionResult(
        method='test',
        hypotheses=[
            Blame(
                span_id='evt_plan',
                step_index=1,
                agent_name='planner',
                confidence=0.8,
                rationale='The initial plan omitted the required constraint.',
                evidence=['Search for the cheapest flight.'],
            )
        ],
    )

    context = DiagnoseContext.build(failed_trajectory, report, attribution)
    proposals = ReflexionSuggestion().suggest(
        failed_trajectory,
        context.recovery_report,
    )

    assert context.recovery_target is not None
    assert context.recovery_target.failure_mode.mode_id == 'attribution.root_cause'
    assert proposals[0].target_event_id == 'evt_plan'


def test_reflexion_builds_one_proposal_per_finding(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    proposals = ReflexionSuggestion().suggest(failed_trajectory, diagnostic_report)

    assert len(proposals) == 1
    assert proposals[0].target_event_id == 'evt_plan'
    assert 'refund_policy' in proposals[0].suggestion_text
    assert proposals[0].requires_human_approval is False


def test_recoverer_rejects_reversed_arguments(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    with pytest.raises(TypeError, match='Did you mean'):
        ReflexionSuggestion().suggest(diagnostic_report, failed_trajectory)


def test_critic_uses_matching_verifier(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    verifier = VerifierSpec(
        id='planning_guard',
        description='Validate planning constraints.',
        matches_families=('planning',),
        matches_mode_prefixes=(),
        suggested_code='assert constraints_preserved',
        rationale='Prevents dropped constraints.',
    )

    proposals = CriticRecoverer([verifier]).suggest(
        failed_trajectory,
        diagnostic_report,
    )

    assert len(proposals) == 1
    assert 'planning_guard' in proposals[0].suggestion_text
    assert 'assert constraints_preserved' in proposals[0].suggestion_text


def test_auto_manual_apply_is_idempotent(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    recoverer = AutoManualRules(
        manual_dir=str(tmp_path),
        project='flights',
    )
    proposal = recoverer.suggest(failed_trajectory, diagnostic_report)[0]

    first_path = recoverer.apply(proposal)
    second_path = recoverer.apply(proposal)
    content = (tmp_path / 'flights.md').read_text(encoding='utf-8')

    assert first_path == second_path
    assert content.count(proposal.suggestion_text) == 1


def test_recoverers_return_empty_for_clean_report(
    failed_trajectory: AgentTrajectory,
) -> None:
    report = DiagnosticReport(trace_id=failed_trajectory.trace_id)

    assert ReflexionSuggestion().suggest(failed_trajectory, report) == []
    assert AutoManualRules().suggest(failed_trajectory, report) == []


def test_self_refine_replaces_truncated_output_with_complete_action(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class TruncatedLLM:
        model = 'truncated'

        def complete(self, messages, **kwargs):
            return CompletionResult(text='Before retrying, verify the required', raw={})

    proposal = SelfRefineLoop(TruncatedLLM()).suggest(
        failed_trajectory,
        diagnostic_report,
    )[0]

    assert 'REFINED ACTION:' in proposal.suggestion_text
    assert 'Preserve refund_policy before calling the browser.' in proposal.suggestion_text
    assert proposal.suggestion_text.endswith('side-effecting tool call.')


def test_self_refine_retries_length_and_invalid_json_with_larger_budget(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class AdaptiveLLM:
        model = 'adaptive'

        def __init__(self) -> None:
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            call_index = len(self.calls)
            if call_index == 1:
                return CompletionResult(
                    text='{"critic":"The planner dropped the refund policy."}',
                    raw={'choices': [{'finish_reason': 'length'}]},
                )
            if call_index == 2:
                return CompletionResult(
                    text=(
                        '{"critic":"The planner omitted the refundable-flight '
                        'constraint, causing the browser call to use an invalid '
                        'plan."}'
                    ),
                    raw={'choices': [{'finish_reason': 'stop'}]},
                )
            if call_index == 3:
                return CompletionResult(
                    text='{"wrong_field":"retry carefully"}',
                    raw={'choices': [{'finish_reason': 'stop'}]},
                )
            return CompletionResult(
                text=(
                    '{"refined_action":"Preserve refund_policy in the plan, '
                    'then verify it before calling the browser."}'
                ),
                raw={'choices': [{'finish_reason': 'stop'}]},
            )

    llm = AdaptiveLLM()
    proposal = SelfRefineLoop(llm, max_tokens=512).suggest(
        failed_trajectory,
        diagnostic_report,
    )[0]

    assert len(llm.calls) == 4
    assert [call[1]['max_tokens'] for call in llm.calls] == [512, 2048, 512, 2048]
    assert all(
        call[1]['response_format'] == {'type': 'json_object'}
        for call in llm.calls
    )
    assert 'The planner omitted the refundable-flight constraint' in proposal.suggestion_text
    assert 'Preserve refund_policy in the plan' in proposal.suggestion_text
    assert 'previous response was truncated or invalid' in llm.calls[1][0][1]['content']


def test_self_refine_retries_without_response_format_when_unsupported(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class CompatibleLLM:
        model = 'compat'

        def __init__(self) -> None:
            self.kwargs = []

        def complete(self, messages, **kwargs):
            self.kwargs.append(kwargs)
            if 'response_format' in kwargs:
                raise RuntimeError('response_format is unsupported')
            field = 'critic' if len(self.kwargs) == 2 else 'refined_action'
            value = (
                'The planner dropped the required refund policy before the tool call.'
                if field == 'critic'
                else 'Preserve refund_policy and verify it before calling the browser.'
            )
            return CompletionResult(
                text=f'{{"{field}":"{value}"}}',
                raw={'choices': [{'finish_reason': 'stop'}]},
            )

    llm = CompatibleLLM()
    proposal = SelfRefineLoop(llm).suggest(
        failed_trajectory,
        diagnostic_report,
    )[0]

    assert len(llm.kwargs) == 3
    assert 'response_format' in llm.kwargs[0]
    assert 'response_format' not in llm.kwargs[1]
    assert 'response_format' not in llm.kwargs[2]
    assert 'Preserve refund_policy' in proposal.suggestion_text
