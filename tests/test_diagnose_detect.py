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


def test_heuristic_prefers_structured_constraint_loss_over_later_error() -> None:
    trajectory = AgentTrajectory(trace_id='constraint-loss', goal='Preserve policy')
    trajectory.add_event(
        AgentEvent(
            event_id='decision',
            trace_id=trajectory.trace_id,
            event_type=EventType.AGENT_STEP,
            agent_name='planner',
            step_index=2,
            output='Choose the cheapest option.',
            metadata={
                'dropped_constraint': 'refund_policy',
                'decision_error': 'The refund policy was not verified.',
            },
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='reflection',
            trace_id=trajectory.trace_id,
            event_type=EventType.REFLECTION,
            agent_name='planner',
            step_index=3,
            error='Postcondition failed.',
        )
    )

    report = HeuristicAnalyzer(rule_packs='core').analyze(trajectory)

    assert report.root_cause_event_id == 'decision'
    assert report.root_cause_step_index == 2
    assert report.findings[0].failure_mode.mode_id == 'planning.constraint_ignorance'
    assert report.findings[0].confidence == 0.95


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


def test_llm_judge_defaults_are_unchanged_by_the_new_parameters(
    failed_trajectory: AgentTrajectory,
) -> None:
    """Constructing the judge the old way must render and select the old way.

    Both new parameters exist so an experiment can vary them; the moment they
    change anything by default, every number this library has already produced
    stops being comparable to the next one.
    """
    from agentdebug.diagnose.detect.judge import LLMJudgeAnalyzer
    from agentdebug.diagnose.detect.selection import earliest_finding

    prompts = []

    class RecordingLLM:
        model = 'fake-judge'

        def complete(self, messages, **kwargs):
            prompts.append(messages[-1]['content'])
            return CompletionResult(text='not json', raw={})

    analyzer = LLMJudgeAnalyzer(RecordingLLM())

    assert analyzer.context_builder is None
    assert analyzer.root_selector is earliest_finding

    analyzer.analyze(failed_trajectory)

    # The flat renderer's signature shape, not the graded one's.
    assert 'event_id=' in prompts[0]
    assert '--- step=' not in prompts[0]


def test_llm_judge_uses_a_context_builder_when_given_one(
    failed_trajectory: AgentTrajectory,
) -> None:
    from agentdebug.diagnose.detect.judge import LLMJudgeAnalyzer

    prompts = []

    class RecordingLLM:
        model = 'fake-judge'

        def complete(self, messages, **kwargs):
            prompts.append(messages[-1]['content'])
            return CompletionResult(text='not json', raw={})

    class StubBuilder:
        def render_chunk(self, events, chunk):
            return 'GRADED-CONTEXT-MARKER'

    LLMJudgeAnalyzer(RecordingLLM(), context_builder=StubBuilder()).analyze(
        failed_trajectory
    )

    assert 'GRADED-CONTEXT-MARKER' in prompts[0]
    assert 'event_id=' not in prompts[0]


def test_the_original_pipeline_is_what_you_get_by_default(
    failed_trajectory: AgentTrajectory,
) -> None:
    """Everything ported from TrajDebug is opt-in, and stays opt-in.

    The port added four components across two stages. If any of them ever
    becomes a default, existing users silently change behaviour and start
    paying for LLM calls they did not ask for -- so the default set is asserted
    by name rather than by intent.
    """
    from agentdebug.diagnose.registry import list_components

    defaults = {
        component.id
        for component in list_components()
        if component.enabled_by_default
    }

    assert defaults == {
        'detect.heuristic',
        'detect.rules.core',
        'attribute.heuristic',
        'recover.deepdebug',
    }
    for ported in (
        'detect.stage_a',
        'detect.perstep',
        'detect.trajdebug',
        'attribute.trajdebug',
    ):
        assert ported not in defaults


def test_both_pipelines_run_on_the_same_trajectory(
    failed_trajectory: AgentTrajectory,
) -> None:
    """The original and the ported pipeline are independently selectable.

    Neither construction reaches into the other: the original needs no LLM at
    all, and the ported one is assembled explicitly from named components.
    """
    from agentdebug.diagnose.pipeline import DiagnosePipeline
    from agentdebug.diagnose.registry import load_component

    original = DiagnosePipeline()          # no arguments: rule packs, no model
    original_result = original.run(failed_trajectory)
    assert original_result.report is not None

    class FakeLLM:
        model = 'fake'

        def complete(self, messages, **kwargs):
            return CompletionResult(text='{"triggers":[],"summary":"none"}', raw={})

    Detector = load_component('detect.trajdebug')
    Attributor = load_component('attribute.trajdebug')
    ported = DiagnosePipeline(
        detector=Detector(FakeLLM()),
        attributor=Attributor(),
    )
    ported_result = ported.run(failed_trajectory)
    assert ported_result.report is not None

    # Same input, two independent pipelines, two independent reports.
    assert original_result.report is not ported_result.report
