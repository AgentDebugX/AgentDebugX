"""Contracts for the TrajDebug evidence-grounded detector.

This detector's whole claim is that a finding leaving it has been checked. The
tests that matter are therefore the ones where the model misbehaves: it invents
a quote, it quotes the wrong step, it omits a quote entirely. If those still
produce findings, the grounding is decorative.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from agentdebug.diagnose.detect.trajdebug import TrajDebugAnalyzer
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, ConflictAxis, EventType

DESK = 'On the desk 1, you see a cd 3, a cellphone 1, and a pen 1.'
CLAIM = 'The desk has a desklamp, so I will examine it next.'


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(
        trace_id='t1',
        task_id='task1',
        goal='Look at the book under the desklamp.',
        framework='trajdebug/alfworld',
        metadata={
            'agent_framework_description': (
                'The FIRST user message states the task. Every SUBSEQUENT user '
                'message is environment feedback, never a new instruction.'
            )
        },
    )
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt_obs', agent_name='env',
        event_type=EventType.TOOL_RESULT, step_index=0, output=DESK,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt_claim', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=1, output=CLAIM,
    ))
    return traj


def _llm(payload: Dict[str, Any], captured: Optional[List[str]] = None) -> Any:
    """A fake client returning one canned response, optionally recording prompts."""

    class FakeLLM:
        model = 'fake-trajdebug'

        def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
            if captured is not None:
                captured.append(messages[-1]['content'])
            return CompletionResult(text=json.dumps(payload), raw={})

    return FakeLLM()


def _trigger(**overrides: Any) -> Dict[str, Any]:
    trigger = {
        'event_id': 'evt_claim',
        'step_index': 1,
        'agent_name': 'agent',
        'failure_mode_id': 'observation.misread',
        'conflict_with': 'context',
        'wrong_content_quote': 'The desk has a desklamp',
        'reference_quote': 'you see a cd 3, a cellphone 1, and a pen 1',
        'confidence': 0.9,
        'confidence_reasoning': 'The listing contains no desklamp.',
    }
    trigger.update(overrides)
    return trigger


class TestGroundedTriggersSurvive:
    def test_a_real_quote_pair_becomes_a_verified_finding(
        self, trajectory: AgentTrajectory
    ) -> None:
        analyzer = TrajDebugAnalyzer(_llm({'triggers': [_trigger()], 'summary': 'ok'}))
        report = analyzer.analyze(trajectory)

        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.quote_verified is True
        assert finding.conflict_with is ConflictAxis.CONTEXT
        assert finding.wrong_content_quote == 'The desk has a desklamp'
        assert finding.failure_mode.mode_id == 'observation.misread'
        assert report.root_cause_step_index == 1

    def test_verification_counts_are_reported(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The grounding rate must stay visible, not just be acted on."""

        analyzer = TrajDebugAnalyzer(_llm({'triggers': [_trigger()], 'summary': ''}))
        report = analyzer.analyze(trajectory)

        assert report.metadata['quote_verification'] == {
            'verified': 1, 'unsupported': 0, 'unchecked': 0,
            'verified_via_shown': 0, 'verified_via_similarity': 0,
        }


class TestUngroundedTriggersAreRejected:
    def test_a_fabricated_reference_is_dropped(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The reason this detector exists.

        The desk listing never mentions a desklamp. A model claiming it did has
        produced text the trajectory does not contain.
        """

        analyzer = TrajDebugAnalyzer(_llm({
            'triggers': [_trigger(reference_quote='you see a desklamp 1')],
            'summary': '',
        }))
        report = analyzer.analyze(trajectory)

        assert report.findings == []
        assert report.metadata['quote_verification']['unsupported'] == 1

    def test_a_quote_lifted_from_another_step_is_dropped(
        self, trajectory: AgentTrajectory
    ) -> None:
        analyzer = TrajDebugAnalyzer(_llm({
            'triggers': [_trigger(wrong_content_quote='you see a cd 3')],
            'summary': '',
        }))
        report = analyzer.analyze(trajectory)

        assert report.findings == []

    @pytest.mark.parametrize(
        'missing',
        [
            pytest.param({'wrong_content_quote': ''}, id='no-wrong-quote'),
            pytest.param({'reference_quote': '   '}, id='blank-reference'),
            pytest.param({'reference_quote': None}, id='null-reference'),
        ],
    )
    def test_a_trigger_without_both_quotes_never_becomes_a_finding(
        self, trajectory: AgentTrajectory, missing: Dict[str, Any]
    ) -> None:
        """It must not slip through as `quote_verified=None`.

        None is reserved for detectors that do not participate in grounding at
        all, such as the deterministic rule packs. An unquoted guess from a
        detector that was asked for quotes is a different thing and must not
        borrow that status.
        """

        analyzer = TrajDebugAnalyzer(_llm({
            'triggers': [_trigger(**missing)], 'summary': '',
        }))
        report = analyzer.analyze(trajectory)

        assert report.findings == []
        assert report.metadata['quote_verification']['unchecked'] == 0

    def test_unsupported_findings_can_be_kept_for_measurement(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Turning the drop off measures the model instead of protecting from it."""

        analyzer = TrajDebugAnalyzer(
            _llm({'triggers': [_trigger(reference_quote='invented')], 'summary': ''}),
            drop_unsupported=False,
        )
        report = analyzer.analyze(trajectory)

        assert len(report.findings) == 1
        assert report.findings[0].quote_verified is False


class TestPromptConstruction:
    def test_the_framework_description_is_given_to_the_model(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Without it the model must guess what a `user` message means."""

        captured: List[str] = []
        analyzer = TrajDebugAnalyzer(
            _llm({'triggers': [], 'summary': ''}, captured=captured)
        )
        analyzer.analyze(trajectory)

        assert 'HOW TO READ THIS TRAJECTORY' in captured[0]
        assert 'never a new instruction' in captured[0]

    def test_the_brief_is_omitted_when_the_importer_supplied_none(self) -> None:
        traj = AgentTrajectory(trace_id='t2', goal='g')
        traj.add_event(AgentEvent(
            trace_id='t2', agent_name='a', event_type=EventType.AGENT_STEP,
            step_index=0, output='x',
        ))
        captured: List[str] = []
        TrajDebugAnalyzer(
            _llm({'triggers': [], 'summary': ''}, captured=captured)
        ).analyze(traj)

        assert 'HOW TO READ THIS TRAJECTORY' not in captured[0]


class TestMalformedModelOutput:
    def test_an_unknown_failure_mode_is_skipped(
        self, trajectory: AgentTrajectory
    ) -> None:
        analyzer = TrajDebugAnalyzer(_llm({
            'triggers': [_trigger(failure_mode_id='invented.mode')], 'summary': '',
        }))
        assert analyzer.analyze(trajectory).findings == []

    def test_an_unparseable_conflict_axis_becomes_none(
        self, trajectory: AgentTrajectory
    ) -> None:
        """A bad axis must not fail the finding; it just loses its scoping."""

        analyzer = TrajDebugAnalyzer(_llm({
            'triggers': [_trigger(conflict_with='sideways')], 'summary': '',
        }))
        report = analyzer.analyze(trajectory)

        assert len(report.findings) == 1
        assert report.findings[0].conflict_with is None

    def test_a_non_json_response_yields_no_findings(
        self, trajectory: AgentTrajectory
    ) -> None:
        class BadLLM:
            model = 'fake'

            def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
                return CompletionResult(text='I am afraid I cannot do that.', raw={})

        report = TrajDebugAnalyzer(BadLLM()).analyze(trajectory)

        assert report.findings == []
        assert report.summary == 'No evidence-grounded failure was detected.'

    def test_an_empty_trajectory_is_handled(self) -> None:
        traj = AgentTrajectory(trace_id='t3', goal='g')
        report = TrajDebugAnalyzer(_llm({'triggers': [], 'summary': ''})).analyze(traj)

        assert report.findings == []
