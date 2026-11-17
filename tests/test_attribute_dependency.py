"""Contracts for the dependency-graph attributor.

The scenario this exists for: a planner picks a value, a later step passes that exact value
to a tool, and the tool rejects it. Step-order attributors blame whichever step is earliest;
this one blames the step that *produced* the value, and says which value it followed.
"""

from __future__ import annotations

import pytest

from agentdebug.diagnose.attribute.dependency import DependencyGraphAttributor
from agentdebug.runtime import TokenUsage
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    ConflictAxis,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
)

MODE = FailureMode(
    mode_id='action.parameter_error',
    name='Parameter error',
    family='action',
    description='The right tool was called with a malformed argument.',
)


def _trajectory() -> AgentTrajectory:
    """A value chosen at step 1, reused at step 3, rejected at step 4."""
    trajectory = AgentTrajectory(trace_id='trace_dep', goal='Book a refundable flight.')
    trajectory.add_event(
        AgentEvent(
            event_id='evt_plan',
            trace_id='trace_dep',
            agent_name='planner',
            event_type=EventType.PLAN,
            step_index=1,
            output='Use refund_policy=waived for this booking.',
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='evt_search',
            trace_id='trace_dep',
            agent_name='search',
            event_type=EventType.TOOL_RESULT,
            step_index=2,
            output='Two fares found.',
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='evt_book',
            trace_id='trace_dep',
            agent_name='browser',
            event_type=EventType.TOOL_CALL,
            step_index=3,
            input={'tool': 'book_flight', 'args': {'refund_policy': 'waived'}},
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='evt_result',
            trace_id='trace_dep',
            agent_name='browser',
            event_type=EventType.TOOL_RESULT,
            step_index=4,
            error='Checkout failed: refund_policy is required.',
        )
    )
    return trajectory


def _finding(step: int, quote: str, *, event_id: str = 'evt_book') -> FailureFinding:
    return FailureFinding(
        failure_mode=MODE,
        event_id=event_id,
        step_index=step,
        confidence=0.7,
        wrong_content_quote=quote,
        reference_quote='refund_policy is required',
        conflict_with=ConflictAxis.CONTEXT,
    )


def test_blames_the_step_that_produced_the_value() -> None:
    """The consumer of a bad value is the symptom; the producer is the blame."""

    trajectory = _trajectory()
    result = DependencyGraphAttributor().attribute(
        trajectory, [_finding(3, 'refund_policy=waived')]
    )

    top = result.hypotheses[0]
    assert top.span_id == 'evt_plan'
    assert top.step_index == 1
    assert top.agent_name == 'planner'
    assert 'dependency_producer' in top.sources
    assert 'waived' in ' '.join(top.evidence)
    assert result.raw['paths'][0]['producer_step'] == 1
    assert 'waived' in result.raw['paths'][0]['values']


def test_chain_membership_is_true_when_the_value_reaches_a_later_step() -> None:
    trajectory = _trajectory()
    result = DependencyGraphAttributor().attribute(
        trajectory, [_finding(3, 'refund_policy=waived')]
    )
    assert result.hypotheses[0].chain_membership is True


def test_no_producer_is_not_invented() -> None:
    """A value that appears nowhere earlier must not fabricate an upstream step."""

    trajectory = _trajectory()
    finding = FailureFinding(
        failure_mode=MODE,
        event_id='evt_result',
        step_index=4,
        confidence=0.7,
        wrong_content_quote='sku_9921',
        conflict_with=ConflictAxis.SELF,
    )
    result = DependencyGraphAttributor().attribute(trajectory, [finding])

    top = result.hypotheses[0]
    assert top.step_index == 4
    assert 'no_upstream_producer' in top.sources
    assert result.raw['paths'][0]['producer_step'] is None
    # Nothing downstream consumes a value first seen at the final step, and that is
    # checkable, so it is False rather than unassessed.
    assert top.chain_membership is False


def test_untrackable_values_leave_chain_membership_unassessed() -> None:
    trajectory = _trajectory()
    finding = FailureFinding(
        failure_mode=MODE, event_id='evt_plan', step_index=1, confidence=0.4
    )
    result = DependencyGraphAttributor().attribute(trajectory, [finding])
    assert result.hypotheses[0].chain_membership is None


def test_no_findings_returns_a_self_describing_empty_result() -> None:
    result = DependencyGraphAttributor().attribute(_trajectory())

    assert result.hypotheses == []
    assert result.raw['reason'] == 'no_findings_supplied'
    assert result.raw['requires_findings'] is True
    assert 'findings' in result.raw['detail']


def test_cost_is_zero_and_no_action_is_guessed() -> None:
    """Nothing here spends tokens, and a model-free attributor has nothing to ask."""

    result = DependencyGraphAttributor().attribute(_trajectory(), [_finding(3, 'waived')])

    assert result.usage == TokenUsage()
    assert result.raw['corrected_action']['requested'] is False
    assert result.raw['corrected_action']['reason'] == 'no_llm'


def test_candidate_steps_ranks_the_producer_before_the_consumer() -> None:
    """Sized for BinarySearchAttributor(always_include_steps=...): producers first."""

    trajectory = _trajectory()
    finding = _finding(3, 'refund_policy=waived')
    steps = DependencyGraphAttributor().candidate_steps(trajectory, [finding])

    assert steps == [1, 3]


def test_candidate_steps_falls_back_to_the_finding_step() -> None:
    trajectory = _trajectory()
    finding = FailureFinding(
        failure_mode=MODE,
        event_id='evt_result',
        step_index=4,
        confidence=0.7,
        wrong_content_quote='sku_9921',
        conflict_with=ConflictAxis.SELF,
    )
    assert DependencyGraphAttributor().candidate_steps(trajectory, [finding]) == [4]


def test_duplicate_findings_at_one_step_merge_into_one_hypothesis() -> None:
    trajectory = _trajectory()
    findings = [_finding(3, 'refund_policy=waived'), _finding(3, 'waived')]
    result = DependencyGraphAttributor().attribute(trajectory, findings)

    assert len(result.raw['paths']) == 2
    assert [hypothesis.step_index for hypothesis in result.hypotheses] == [1]


def test_eval_parity_uses_optional_gold_metadata() -> None:
    """Same `raw['eval']` shape AllAtOnce and StepByStep report, and None without gold."""

    trajectory = _trajectory()
    finding = _finding(3, 'refund_policy=waived')
    assert DependencyGraphAttributor().attribute(trajectory, [finding]).raw['eval'] is None

    trajectory.metadata.update({'mistake_agent': 'planner', 'mistake_step': 1})
    metrics = DependencyGraphAttributor().attribute(trajectory, [finding]).raw['eval']

    assert metrics is not None
    assert metrics['exact_step'] is True
    assert metrics['both_exact'] is True


def test_registry_exposes_the_component() -> None:
    from agentdebug.diagnose import get_component_metadata, load_component

    metadata = get_component_metadata('attribute.dependency_graph')
    assert metadata.stage == 'attribute'
    assert metadata.cost_profile == 'free'
    assert 'zero_llm' in metadata.capabilities
    assert load_component('attribute.dependency_graph') is DependencyGraphAttributor


def test_cli_selects_it_without_an_llm_client() -> None:
    """`--attributor dependency-graph` must not require a configured model."""

    from agentdebug.cli import legacy
    from agentdebug.workbench.profiles import ATTRIBUTORS

    assert legacy._ATTRIBUTOR_ALIASES['dependency-graph'] == 'dependency_graph'
    assert 'dependency_graph' not in legacy._LLM_ATTRIBUTORS
    assert 'dependency_graph' in ATTRIBUTORS

    trajectory = _trajectory()
    report = DiagnosticReport(trace_id=trajectory.trace_id, findings=[_finding(3, 'waived')])
    result = legacy._run_attributor('dependency_graph', trajectory, report, None)

    assert result.method == 'dependency_graph'
    assert result.hypotheses[0].step_index == 1


@pytest.mark.parametrize('quote', ['refund_policy', 'waived'])
def test_any_quoted_value_finds_the_producer(quote: str) -> None:
    result = DependencyGraphAttributor().attribute(
        _trajectory(), [_finding(3, quote)]
    )
    assert result.hypotheses[0].step_index == 1
