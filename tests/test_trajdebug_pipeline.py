"""The two TrajDebug components wired together through DiagnosePipeline.

Each is unit-tested in isolation, but the contract that matters in practice is
the seam: the detector's findings have to carry what the attributor clusters on,
and the pipeline has to promote the result the way it does for every other pair
of components. A regression here would not show up in either component's own
tests.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from agentdebug.diagnose.attribute.trajdebug import TrajDebugAttributor
from agentdebug.diagnose.detect.trajdebug import TrajDebugAnalyzer
from agentdebug.diagnose.pipeline import DiagnosePipeline
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

DESK = 'On the desk 1, you see a cd 3, a cellphone 1, and a pen 1.'
CLAIM_A = 'The desk has a desklamp, so I will examine it.'
CLAIM_B = 'Still looking at the desk for the desklamp.'


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(
        trace_id='t1', task_id='task1', goal='Look at the book under the desklamp.'
    )
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt0', agent_name='env',
        event_type=EventType.TOOL_RESULT, step_index=0, output=DESK,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt1', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=1, output=CLAIM_A,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt2', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=2, output=CLAIM_B,
    ))
    return traj


def _scripted(*responses: Dict[str, Any]) -> Any:
    """A client returning each canned response in turn, then repeating the last."""

    class FakeLLM:
        model = 'fake'

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
            payload = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return CompletionResult(text=json.dumps(payload), raw={})

    return FakeLLM()


DETECTION = {
    'triggers': [
        {
            'event_id': 'evt1', 'step_index': 1, 'agent_name': 'agent',
            'failure_mode_id': 'observation.ignored', 'conflict_with': 'context',
            'wrong_content_quote': 'The desk has a desklamp',
            'reference_quote': 'you see a cd 3, a cellphone 1, and a pen 1',
            'confidence': 0.9, 'confidence_reasoning': 'No desklamp in the listing.',
        },
        {
            'event_id': 'evt2', 'step_index': 2, 'agent_name': 'agent',
            'failure_mode_id': 'observation.ignored', 'conflict_with': 'context',
            'wrong_content_quote': 'Still looking at the desk for the desklamp',
            'reference_quote': 'you see a cd 3, a cellphone 1, and a pen 1',
            'confidence': 0.8, 'confidence_reasoning': 'Same listing, still ignored.',
        },
    ],
    'summary': 'Agent ignored the desk listing twice.',
}

STATE = {
    'instances': [{
        'instance_id': 0, 'fix_status': None, 'fix_evidence_quote': None,
        'chain_membership': True, 'terminal_connection': 'direct',
        'wasted_steps': [2],
    }]
}


def test_detector_and_attributor_run_end_to_end(trajectory: AgentTrajectory) -> None:
    pipeline = DiagnosePipeline(
        detector=TrajDebugAnalyzer(_scripted(DETECTION)),
        attributor=TrajDebugAttributor(_scripted(STATE)),
    )
    result = pipeline.run(trajectory)

    # Both findings survived verification -- their quotes are real.
    assert len(result.report.findings) == 2
    assert all(f.quote_verified is True for f in result.report.findings)

    # ...and collapsed into one error, because they violate the same text.
    assert result.attribution is not None
    assert result.attribution.raw['num_instances'] == 1

    blame = result.attribution.hypotheses[0]
    assert blame.step_index == 1, 'the origin, not the last repetition'
    assert blame.chain_membership is True
    assert blame.wasted_steps == [2]


def test_the_pipeline_promotes_the_attribution_onto_the_report(
    trajectory: AgentTrajectory,
) -> None:
    """DiagnoseContext promotes the primary attribution the same way it does
    for every other component pair; this checks the seam is not special-cased."""

    pipeline = DiagnosePipeline(
        detector=TrajDebugAnalyzer(_scripted(DETECTION)),
        attributor=TrajDebugAttributor(_scripted(STATE)),
    )
    result = pipeline.run(trajectory)

    assert result.report.attribution is not None
    assert result.context is not None


def test_clustering_collapses_repeats_before_blame(
    trajectory: AgentTrajectory,
) -> None:
    """Two findings in, one blame out.

    Without C1 the repeated symptom at step 2 would be a second candidate
    competing with its own cause at step 1.
    """

    pipeline = DiagnosePipeline(
        detector=TrajDebugAnalyzer(_scripted(DETECTION)),
        attributor=TrajDebugAttributor(),   # no LLM: C1 + C3 only
    )
    result = pipeline.run(trajectory)

    assert len(result.report.findings) == 2
    assert result.attribution is not None
    assert len(result.attribution.hypotheses) == 1
    assert result.attribution.raw['state_classified'] is False


def test_an_ungrounded_detection_reaches_neither_stage(
    trajectory: AgentTrajectory,
) -> None:
    """A fabricated quote must not survive into attribution.

    This is the property the whole branch rests on: verification happens before
    anything downstream can act on the finding.
    """

    fabricated = {
        'triggers': [dict(DETECTION['triggers'][0],
                          reference_quote='you see a desklamp 1')],
        'summary': '',
    }
    pipeline = DiagnosePipeline(
        detector=TrajDebugAnalyzer(_scripted(fabricated)),
        attributor=TrajDebugAttributor(),
    )
    result = pipeline.run(trajectory)

    assert result.report.findings == []
    assert result.report.metadata['quote_verification']['unsupported'] == 1
    assert result.attribution is not None
    assert result.attribution.hypotheses == []
    assert result.attribution.raw['reason'] == 'no_findings_supplied'


def test_the_zero_llm_default_path_is_untouched(trajectory: AgentTrajectory) -> None:
    """Both components are opt-in; local_default() must not pick them up."""

    result = DiagnosePipeline.local_default().run(trajectory)

    assert result.report.metadata.get('analyzer') != 'TrajDebugAnalyzer'
    if result.attribution is not None:
        assert result.attribution.method != 'trajdebug'
