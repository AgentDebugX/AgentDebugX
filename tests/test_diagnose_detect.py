from __future__ import annotations

from agentdebug.diagnose.detect import (
    DetectorConfig,
    HeuristicAnalyzer,
    RepeatedStateDetector,
    RepeatedToolCallDetector,
    StepCountLimitDetector,
    default_detectors,
    run_detectors,
)
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType


def test_heuristic_analyzer_localizes_failure(
    failed_trajectory: AgentTrajectory,
) -> None:
    report = HeuristicAnalyzer(rule_packs='core').analyze(failed_trajectory)

    assert report.findings
    assert report.root_cause_event_id is not None
    assert report.metadata['rule_packs'] == ['core']


def test_repeated_tool_call_detector_positive_and_negative() -> None:
    trajectory = AgentTrajectory(trace_id='repeat-tools')
    for step in range(3):
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type=EventType.TOOL_CALL,
                agent_name='search',
                step_index=step,
                input={'query': 'same'},
            )
        )

    assert RepeatedToolCallDetector(threshold=3).detect(trajectory)
    assert RepeatedToolCallDetector(threshold=4).detect(trajectory) == []


def test_repeated_state_and_step_limit_detectors() -> None:
    trajectory = AgentTrajectory(trace_id='repeat-state')
    for step in range(4):
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type=EventType.OBSERVATION,
                step_index=step,
                output='unchanged state',
            )
        )

    assert RepeatedStateDetector(window=4, threshold=3).detect(trajectory)
    assert StepCountLimitDetector(max_steps=3).detect(trajectory)


def test_default_detector_config_is_applied() -> None:
    detectors = default_detectors(
        DetectorConfig(
            repeated_tool_call_threshold=5,
            repeated_state_window=6,
            repeated_state_threshold=4,
            step_count_limit=7,
        )
    )

    assert detectors[0].threshold == 5
    assert detectors[1].window == 6
    assert detectors[2].max_steps == 7


def test_detector_runner_isolates_detector_failures(
    failed_trajectory: AgentTrajectory,
) -> None:
    class BrokenDetector:
        id = 'broken'

        def detect(self, trajectory):
            raise RuntimeError('boom')

    class EmptyDetector:
        id = 'empty'

        def detect(self, trajectory):
            return []

    assert run_detectors(failed_trajectory, [BrokenDetector(), EmptyDetector()]) == []


def test_llm_judge_parses_known_and_novel_modes(
    failed_trajectory: AgentTrajectory,
) -> None:
    from agentdebug.diagnose.detect.judge import LLMJudgeAnalyzer
    from agentdebug.schema import SEED_FAILURE_MODES

    known_mode = next(iter(SEED_FAILURE_MODES))

    class FakeLLM:
        model = 'fake-judge'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"findings":['
                    f'{{"event_id":"evt_plan","step_index":1,"agent_name":"planner",'
                    f'"failure_mode_id":"{known_mode}","confidence":0.9,"evidence":["known"]}},'
                    '{"event_id":"evt_tool","step_index":2,"agent_name":"browser",'
                    '"failure_mode_id":"novel.custom","confidence":0.7,"evidence":["novel"]}'
                    '],"summary":"judge summary"}'
                ),
                raw={},
            )

    report = LLMJudgeAnalyzer(FakeLLM()).analyze(failed_trajectory)

    assert len(report.findings) == 1
    assert report.summary == 'judge summary'
    assert report.metadata['novel_mode_candidates'][0]['failure_mode_id'] == 'novel.custom'


def test_llm_judge_handles_non_json_response(
    failed_trajectory: AgentTrajectory,
) -> None:
    from agentdebug.diagnose.detect.judge import LLMJudgeAnalyzer

    class FakeLLM:
        model = 'fake-judge'

        def complete(self, messages, **kwargs):
            return CompletionResult(text='not json', raw={})

    report = LLMJudgeAnalyzer(FakeLLM()).analyze(failed_trajectory)

    assert report.findings == []
    assert report.summary == 'No failure was detected.'
