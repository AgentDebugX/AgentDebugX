from __future__ import annotations

import pytest

from agentdebug.diagnose.recover import (
    AutoManualRules,
    CriticRecoverer,
    ReflexionSuggestion,
    VerifierSpec,
)
from agentdebug.schema import AgentTrajectory, DiagnosticReport


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
