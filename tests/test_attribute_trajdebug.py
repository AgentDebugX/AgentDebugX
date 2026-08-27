"""Contracts for the TrajDebug state-aware attributor.

The scenario this exists for: an agent errs early, notices, corrects, and then
fails later for an unrelated reason. Every step-index-ordered attributor blames
the early error. The test that matters is the one where it does not.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from agentdebug.diagnose.attribute.trajdebug import TrajDebugAttributor
from agentdebug.runtime import CompletionResult
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    ConflictAxis,
    EventType,
    FailureFinding,
    FailureMode,
)

MODE = FailureMode(
    mode_id='observation.ignored',
    name='Observation ignored',
    family='observation',
    description='Available output went unused.',
)


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(trace_id='t1', task_id='task1', goal='Find the book.')
    for step in range(6):
        traj.add_event(AgentEvent(
            trace_id='t1', event_id=f'evt{step}', agent_name='agent',
            event_type=EventType.AGENT_STEP, step_index=step, output=f'step {step} text',
        ))
    return traj


def _finding(step: int, reference: Optional[str], confidence: float = 0.8) -> FailureFinding:
    return FailureFinding(
        failure_mode=MODE,
        event_id=f'evt{step}',
        step_index=step,
        confidence=confidence,
        wrong_content_quote=f'wrong at {step}',
        reference_quote=reference,
        conflict_with=ConflictAxis.CONTEXT,
    )


def _llm(payload: Dict[str, Any]) -> Any:
    class FakeLLM:
        model = 'fake-state'

        def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
            return CompletionResult(text=json.dumps(payload), raw={})

    return FakeLLM()


class TestClustering:
    def test_findings_violating_the_same_thing_become_one_instance(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Twenty repetitions of one error are one error.

        Without this, a repeated symptom outvotes a single decisive cause purely
        by showing up more often.
        """

        findings = [
            _finding(1, 'the desk holds no desklamp'),
            _finding(3, 'the desk holds no desklamp'),
            _finding(5, 'the desk holds no desklamp'),
        ]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert result.raw['num_findings'] == 3
        assert result.raw['num_instances'] == 1
        assert len(result.hypotheses) == 1
        assert result.hypotheses[0].step_index == 1  # the origin, not the last

    def test_clustering_ignores_case_and_whitespace(
        self, trajectory: AgentTrajectory
    ) -> None:
        findings = [
            _finding(1, 'The Desk Holds   No Desklamp'),
            _finding(2, 'the desk holds no desklamp'),
        ]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert result.raw['num_instances'] == 1

    def test_different_violations_stay_separate(
        self, trajectory: AgentTrajectory
    ) -> None:
        findings = [
            _finding(1, 'the desk holds no desklamp'),
            _finding(2, 'the deadline is Friday'),
        ]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert result.raw['num_instances'] == 2

    def test_findings_without_a_reference_are_not_merged(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Conservative on purpose: merging two real errors hides one of them."""

        findings = [_finding(1, None), _finding(2, None)]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert result.raw['num_instances'] == 2


class TestStateChangesTheAnswer:
    def test_a_recovered_error_loses_to_a_later_live_one(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The entire reason this attributor exists.

        Step 1 is earliest, so every index-ordered attributor blames it. But the
        agent fixed it, and the run failed because of step 4. C2 says so and C3
        acts on it.
        """

        findings = [
            _finding(1, 'the desk holds no desklamp'),
            _finding(4, 'the deadline is Friday'),
        ]
        states = {'instances': [
            {'instance_id': 0, 'fix_status': 'fixed_at_step_3',
             'fix_evidence_quote': 'Actually the desklamp is on the dresser.',
             'chain_membership': False, 'terminal_connection': None, 'wasted_steps': [2, 3]},
            {'instance_id': 1, 'fix_status': None, 'fix_evidence_quote': None,
             'chain_membership': True, 'terminal_connection': 'direct', 'wasted_steps': []},
        ]}
        result = TrajDebugAttributor(_llm(states)).attribute(trajectory, findings)

        primary = result.hypotheses[0]
        assert primary.step_index == 4, 'the recovered early error must not win'
        assert primary.chain_membership is True

        recovered = result.hypotheses[1]
        assert recovered.step_index == 1
        assert recovered.fix_status == 'fixed_at_step_3'
        assert recovered.fix_evidence_quote == 'Actually the desklamp is on the dresser.'
        assert recovered.wasted_steps == [2, 3]

    def test_without_state_the_earliest_still_wins(
        self, trajectory: AgentTrajectory
    ) -> None:
        """No model means no state, and absence of evidence must not exclude."""

        findings = [
            _finding(1, 'the desk holds no desklamp'),
            _finding(4, 'the deadline is Friday'),
        ]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert result.hypotheses[0].step_index == 1
        assert result.hypotheses[0].chain_membership is None
        assert result.raw['state_classified'] is False

    def test_budget_debt_is_carried_onto_the_blame(
        self, trajectory: AgentTrajectory
    ) -> None:
        findings = [_finding(2, 'the desk holds no desklamp')]
        states = {'instances': [{
            'instance_id': 0, 'fix_status': None, 'fix_evidence_quote': None,
            'chain_membership': True, 'terminal_connection': 'budget_debt',
            'wasted_steps': [3, 4, 5],
        }]}
        result = TrajDebugAttributor(_llm(states)).attribute(trajectory, findings)

        assert result.hypotheses[0].terminal_connection == 'budget_debt'
        assert result.hypotheses[0].wasted_steps == [3, 4, 5]


class TestDegradation:
    def test_no_findings_returns_a_self_describing_empty(
        self, trajectory: AgentTrajectory
    ) -> None:
        """An empty result must not look like 'nothing to blame here'."""

        result = TrajDebugAttributor().attribute(trajectory, [])

        assert result.hypotheses == []
        assert result.raw['reason'] == 'no_findings_supplied'
        assert result.raw['requires_findings'] is True

    def test_a_non_json_state_response_degrades_to_clustering(
        self, trajectory: AgentTrajectory
    ) -> None:
        class BadLLM:
            model = 'fake'

            def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
                return CompletionResult(text='sorry, no', raw={})

        findings = [_finding(1, 'a'), _finding(3, 'b')]
        result = TrajDebugAttributor(BadLLM()).attribute(trajectory, findings)

        assert len(result.hypotheses) == 2
        assert result.hypotheses[0].chain_membership is None

    def test_a_transport_failure_degrades_to_clustering(
        self, trajectory: AgentTrajectory
    ) -> None:
        class ExplodingLLM:
            model = 'fake'

            def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
                raise RuntimeError('connection reset')

        result = TrajDebugAttributor(ExplodingLLM()).attribute(
            trajectory, [_finding(1, 'a')]
        )

        assert len(result.hypotheses) == 1
        assert result.hypotheses[0].chain_membership is None


class TestBlameShape:
    def test_blame_carries_the_quote_pair_as_evidence(
        self, trajectory: AgentTrajectory
    ) -> None:
        result = TrajDebugAttributor().attribute(
            trajectory, [_finding(2, 'the desk holds no desklamp')]
        )
        blame = result.hypotheses[0]

        assert 'wrong at 2' in blame.evidence
        assert 'the desk holds no desklamp' in blame.evidence
        assert blame.sources == ['trajdebug']
        assert blame.span_id == 'evt2'

    def test_rationale_names_how_many_findings_support_the_instance(
        self, trajectory: AgentTrajectory
    ) -> None:
        findings = [_finding(1, 'same thing'), _finding(2, 'same thing')]
        result = TrajDebugAttributor().attribute(trajectory, findings)

        assert '2 finding(s)' in result.hypotheses[0].rationale


def test_rank_policy_defaults_to_earliest(trajectory: AgentTrajectory) -> None:
    """The new knob must not move the existing answer when left alone."""
    attributor = TrajDebugAttributor()
    assert attributor.rank_policy == 'earliest'

    findings = [_finding(1, 'alpha', confidence=0.2), _finding(4, 'beta', confidence=0.95)]
    result = attributor.attribute(trajectory, findings)

    assert result.hypotheses[0].step_index == 1


def test_confident_rank_policy_prefers_the_detector_confidence(
    trajectory: AgentTrajectory,
) -> None:
    attributor = TrajDebugAttributor(rank_policy='confident')

    findings = [_finding(1, 'alpha', confidence=0.2), _finding(4, 'beta', confidence=0.95)]
    result = attributor.attribute(trajectory, findings)

    assert result.hypotheses[0].step_index == 4


def test_unknown_rank_policy_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        TrajDebugAttributor(rank_policy='whatever')
