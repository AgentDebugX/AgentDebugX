from __future__ import annotations

from agentdebug.diagnose.attribute import AllAtOnceAttributor, HeuristicAttributor
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentTrajectory, FailureFinding, FailureMode


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
