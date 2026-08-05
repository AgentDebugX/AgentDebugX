from __future__ import annotations

from agentdebug.diagnose.attribute import AllAtOnceAttributor, HeuristicAttributor
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, FailureFinding, FailureMode


def test_heuristic_attributor_prefers_earliest_finding(
    failed_trajectory: AgentTrajectory,
    failure_mode: FailureMode,
) -> None:
    later = FailureFinding(
        failure_mode=failure_mode,
        event_id='evt_tool',
        agent_name='browser',
        step_index=2,
        confidence=0.99,
    )
    earlier = FailureFinding(
        failure_mode=failure_mode,
        event_id='evt_plan',
        agent_name='planner',
        step_index=1,
        confidence=0.4,
    )

    result = HeuristicAttributor().attribute(failed_trajectory, [later, earlier])

    assert result.hypotheses[0].span_id == 'evt_plan'
    assert result.hypotheses[0].agent_name == 'planner'


def test_heuristic_attributor_handles_no_findings(
    failed_trajectory: AgentTrajectory,
) -> None:
    assert HeuristicAttributor().attribute(failed_trajectory).hypotheses == []


def test_heuristic_attributor_says_why_it_returned_nothing(
    failed_trajectory: AgentTrajectory,
) -> None:
    """An empty result must be distinguishable from "nothing to blame in this trajectory".

    A downstream harness used the bare attributor as a fallback for a failing
    DiagnosePipeline and measured heuristic returning no hypotheses on 5 of 5 trajectories.
    Those trajectories were not clean: a findings-less call is empty by construction, and
    under the pipeline the same attributor produced hypotheses on 3 of the 5. The emptiness
    now carries its own explanation.
    """
    result = HeuristicAttributor().attribute(failed_trajectory)
    assert result.hypotheses == []
    assert result.raw['reason'] == 'no_findings_supplied'
    assert 'findings' in result.raw['detail']


def test_requires_findings_lets_a_caller_check_before_spending() -> None:
    """Callers choose attributors dynamically; this is how they avoid a guaranteed-empty call."""
    assert HeuristicAttributor.requires_findings is True
    # Attributors that read the trajectory directly need not declare it; absent means False.
    assert getattr(AllAtOnceAttributor, 'requires_findings', False) is False


def test_all_at_once_normalizes_ordinal_to_real_event(
    failed_trajectory: AgentTrajectory,
) -> None:
    class FakeLLM:
        model = 'fake-attributor'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"span_id":null,"step_index":0,"agent_name":"planner",'
                    '"confidence":0.75,"rationale":"first attributable step",'
                    '"evidence":["omitted constraint"]}'
                ),
                raw={},
            )

    result = AllAtOnceAttributor(FakeLLM()).attribute(failed_trajectory)

    assert result.hypotheses[0].span_id == 'evt_plan'
    assert result.hypotheses[0].step_index == 1
    assert result.hypotheses[0].agent_name == 'planner'


def test_all_at_once_falls_back_on_invalid_json(
    failed_trajectory: AgentTrajectory,
    failure_mode: FailureMode,
) -> None:
    finding = FailureFinding(
        failure_mode=failure_mode,
        event_id='evt_plan',
        agent_name='planner',
        step_index=1,
        confidence=0.6,
    )

    class FakeLLM:
        model = 'fake-attributor'

        def complete(self, messages, **kwargs):
            return CompletionResult(text='invalid', raw={})

    result = AllAtOnceAttributor(FakeLLM()).attribute(
        failed_trajectory,
        [finding],
    )

    assert result.method == 'heuristic'
    assert result.hypotheses[0].span_id == 'evt_plan'


def test_all_at_once_anchors_same_step_blame_to_detector_event() -> None:
    trajectory = AgentTrajectory(trace_id='same-step')
    trajectory.add_event(
        AgentEvent(
            event_id='thought',
            trace_id=trajectory.trace_id,
            event_type='agent.step',
            agent_name='agent',
            step_index=2,
            output='Choose invalid option.',
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='action',
            trace_id=trajectory.trace_id,
            event_type='tool.call',
            agent_name='agent',
            step_index=2,
            input={'option': 'invalid'},
        )
    )
    finding = FailureFinding(
        failure_mode=FailureMode(
            mode_id='planning.constraint_ignorance',
            name='Constraint ignorance',
            family='planning',
            description='Constraint dropped.',
        ),
        event_id='thought',
        agent_name='agent',
        step_index=2,
        evidence=['Choose invalid option.'],
    )

    class FakeLLM:
        model = 'fake-attributor'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"span_id":"action","step_index":2,"agent_name":"agent",'
                    '"confidence":0.9,"rationale":"bad execution",'
                    '"evidence":["invalid"]}'
                ),
                raw={},
            )

    result = AllAtOnceAttributor(FakeLLM()).attribute(trajectory, [finding])

    assert result.hypotheses[0].span_id == 'thought'
    assert 'detector_event_anchor' in result.hypotheses[0].sources
