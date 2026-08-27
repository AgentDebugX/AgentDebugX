"""Contracts for the per-step detector.

This detector's claim is structural: it asks about one step at a time, so the
question it answers ("is *this* step wrong?") is different from the one the
chunked detectors answer ("rank what went wrong in these 80 events"). The tests
that matter are therefore about scope --- which events get judged, what the
model is shown when judging them, and what happens when it says no.

The fire-rate test is here for a measured reason. TrajDebug's own Stage B
emitted a trigger on 96.8% of the steps it judged on ALFWorld, and a detector
that flags everything has located nothing. The rate is recorded on every report
so it stays visible rather than being inferred later from bad results.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from agentdebug.diagnose.detect.perstep import PerStepAnalyzer
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, ConflictAxis, EventType

GOAL = 'Add a retry to the upload helper.'
PLAN = 'I will edit upload.py and add three retries.'
RESULT = 'error: upload.py not found in this repository'
LATER = 'Editing upload.py now to add the retry loop.'


class ScriptedLLM:
    """Replies from a queue, recording every prompt it was given."""

    model = 'stub'

    def __init__(self, replies: List[Any]) -> None:
        self.replies = list(replies)
        self.prompts: List[str] = []
        self.kwargs: List[Dict[str, Any]] = []

    def complete(self, messages, **kwargs) -> CompletionResult:
        self.prompts.append(messages[-1]['content'])
        self.kwargs.append(kwargs)
        reply = self.replies.pop(0) if self.replies else {'is_error': False}
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return CompletionResult(text=text, raw={})


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(
        trace_id='t1', task_id='task1', goal=GOAL, framework='trajdebug/swebenchpro',
    )
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt_plan', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=0, output=PLAN,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt_tool', agent_name='env',
        event_type=EventType.TOOL_RESULT, step_index=1, output=RESULT,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', event_id='evt_later', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=2, output=LATER,
    ))
    return traj


def trigger(**overrides: Any) -> Dict[str, Any]:
    payload = {
        'is_error': True,
        'failure_mode_id': next(iter(__import__(
            'agentdebug.schema', fromlist=['SEED_FAILURE_MODES']
        ).SEED_FAILURE_MODES)),
        'conflict_with': 'context',
        'wrong_content_quote': LATER,
        'reference_quote': RESULT,
        'confidence': 0.8,
        'reasoning': 'proceeds despite the file not existing',
    }
    payload.update(overrides)
    return payload


# -- scope ---------------------------------------------------------------

def test_only_agent_authored_steps_are_judged(trajectory):
    """The environment speaking is not the agent erring.

    Judging a tool result would let the detector blame the environment for the
    agent's mistake, which is the category error the conflict axis exists to
    prevent.
    """
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert len(llm.prompts) == 2          # the two agent.step events, not the tool result
    assert report.metadata['steps_judged'] == 2


def test_one_call_per_step_not_one_per_chunk(trajectory):
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    PerStepAnalyzer(llm).analyze(trajectory)

    assert len(llm.prompts) == 2


def test_the_focus_step_is_shown_in_full_and_named(trajectory):
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    PerStepAnalyzer(llm).analyze(trajectory)

    assert 'THE STEP YOU ARE JUDGING' in llm.prompts[0]
    assert PLAN in llm.prompts[0]


def test_history_is_before_only(trajectory):
    """A step cannot be judged using text the agent had not yet seen."""
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    PerStepAnalyzer(llm).analyze(trajectory)

    first = llm.prompts[0]
    history = first.split('=== THE STEP YOU ARE JUDGING ===')[0]
    assert RESULT not in history          # step 1 is after step 0
    assert LATER not in history


def test_later_steps_reach_the_history_of_later_focuses(trajectory):
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    PerStepAnalyzer(llm).analyze(trajectory)

    second = llm.prompts[1]
    history = second.split('=== THE STEP YOU ARE JUDGING ===')[0]
    assert RESULT in history


# -- verdicts ------------------------------------------------------------

def test_a_clean_run_produces_no_findings(trajectory):
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert report.findings == []
    assert report.root_cause_step_index is None
    assert 'No failure was detected' in report.summary


def test_a_trigger_becomes_a_grounded_finding(trajectory):
    llm = ScriptedLLM([{'is_error': False}, trigger()])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.step_index == 2
    assert finding.wrong_content_quote == LATER
    assert finding.reference_quote == RESULT
    assert finding.conflict_with is ConflictAxis.CONTEXT


def test_a_trigger_without_both_quotes_is_discarded(trajectory):
    """Half a citation cannot be verified, so it is not a finding."""
    llm = ScriptedLLM([{'is_error': False}, trigger(reference_quote='')])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert report.findings == []


def test_an_invented_quote_is_dropped_by_verification(trajectory):
    llm = ScriptedLLM([
        {'is_error': False},
        trigger(wrong_content_quote='I deleted the production database'),
    ])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert report.findings == []
    assert report.metadata['quote_verification']['unsupported'] >= 1


def test_an_unparseable_reply_is_counted_not_guessed(trajectory):
    llm = ScriptedLLM(['I cannot answer that', {'is_error': False}])

    analyzer = PerStepAnalyzer(llm)
    report = analyzer.analyze(trajectory)

    assert report.findings == []
    assert analyzer.stats['parse_failures'] == 1


def test_an_unknown_mode_id_keeps_the_finding(trajectory):
    """A located error with real quotes survives a bad label.

    Losing the step because the taxonomy name was wrong would throw away the
    part that was right.
    """
    llm = ScriptedLLM([{'is_error': False}, trigger(failure_mode_id='not.a.real.mode')])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert len(report.findings) == 1
    assert report.findings[0].failure_mode.mode_id == 'unknown.unlabeled'


# -- the fire rate -------------------------------------------------------

def test_fire_rate_is_recorded_on_every_report(trajectory):
    """TrajDebug's Stage B fired on 96.8% of judged steps on ALFWorld.

    At that rate "earliest flagged" and "earliest step" are the same answer, so
    the rate has to be visible on the report rather than reconstructed later.
    """
    llm = ScriptedLLM([trigger(wrong_content_quote=PLAN, reference_quote=GOAL,
                               conflict_with='task'),
                       {'is_error': False}])

    report = PerStepAnalyzer(llm).analyze(trajectory)

    assert report.metadata['steps_judged'] == 2
    assert report.metadata['steps_flagged'] == 1
    assert report.metadata['fire_rate'] == pytest.approx(0.5)


def test_json_mode_is_requested_by_default(trajectory):
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    PerStepAnalyzer(llm).analyze(trajectory)

    assert llm.kwargs[0]['response_format'] == {'type': 'json_object'}


def test_compressions_are_used_for_history_when_supplied(trajectory):
    pool = {0: {'th1': 'POOLED-STEP-0', 'th2': 'POOLED-STEP-0', 'th3': 'POOLED-STEP-0'},
            1: {'th1': 'POOLED-STEP-1', 'th2': 'POOLED-STEP-1', 'th3': 'POOLED-STEP-1'},
            2: {'th1': 'POOLED-STEP-2', 'th2': 'POOLED-STEP-2', 'th3': 'POOLED-STEP-2'}}
    llm = ScriptedLLM([{'is_error': False}, {'is_error': False}])

    report = PerStepAnalyzer(llm, compressions=pool).analyze(trajectory)

    history = llm.prompts[1].split('=== THE STEP YOU ARE JUDGING ===')[0]
    assert 'POOLED-STEP-1' in history
    assert report.metadata['used_compressions'] is True
    # The focus step itself is never pooled -- it is what the model must quote.
    assert LATER in llm.prompts[1]
