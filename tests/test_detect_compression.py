"""Contracts for Stage A compression and graded rendering.

The claim this module makes is about *where the loss lands*. A flat renderer
loses the same fraction of every step including the one under judgement; a
graded one spends its budget near the focus and lets distant steps go terse.
So the tests that matter are the ones about allocation: which steps arrive at
which tier, what happens when the cap binds, and which steps never reach the
model at all.

The two short-circuits get their own tests for a different reason. They are
what makes this affordable, and a short-circuit that silently stops firing
turns a $2 run into a $40 one with no other symptom.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from agentdebug.diagnose.detect.compression import (
    GradedContextBuilder,
    StepCompressor,
    clip_middle,
    compress_role_for,
    event_text,
    looks_machine_generated,
    render_history_for_focus,
    select_tier,
)
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "app.py", line 12, in main\n'
    '    run()\n'
    '  File "app.py", line 7, in run\n'
    '    raise ValueError("boom")\n'
    'ValueError: boom\n'
)


class RecordingLLM:
    """Counts calls, so a test can assert the model was never reached."""

    model = 'stub'

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, **kwargs) -> CompletionResult:
        self.calls.append({'messages': messages, 'kwargs': kwargs})
        return CompletionResult(text=self.payload, raw={})


def make_event(step: int, text: str, event_type: EventType = EventType.AGENT_STEP) -> AgentEvent:
    return AgentEvent(
        trace_id='t1', event_id=f'evt_{step}', agent_name='agent',
        event_type=event_type, step_index=step, output=text,
    )


def make_trajectory(events: List[AgentEvent]) -> AgentTrajectory:
    traj = AgentTrajectory(trace_id='t1', task_id='task1', goal='g', framework='f')
    for event in events:
        traj.add_event(event)
    return traj


# -- clip_middle ---------------------------------------------------------

def test_clip_middle_keeps_both_ends():
    text = 'HEAD' + ('x' * 400) + 'TAIL'
    out = clip_middle(text, 120)
    assert out.startswith('HEAD')
    assert out.endswith('TAIL')


def test_clip_middle_never_exceeds_the_limit():
    text = 'y' * 5000
    for limit in (5, 20, 41, 80, 256, 1024):
        assert len(clip_middle(text, limit)) <= limit, limit


def test_clip_middle_leaves_short_text_alone():
    assert clip_middle('short', 100) == 'short'


# -- short-circuits ------------------------------------------------------

def test_small_step_is_passed_through_without_a_call():
    llm = RecordingLLM('{"th1":"a","th2":"b","th3":"c"}')
    compressor = StepCompressor(llm)
    traj = make_trajectory([make_event(0, 'tiny')])

    pool = compressor.compress(traj)

    assert llm.calls == []
    assert compressor.stats['skipped_small'] == 1
    assert pool[0]['th1'] == pool[0]['th3'] == 'output: tiny'


def test_machine_generated_environment_step_is_clipped_not_summarised():
    llm = RecordingLLM('{"th1":"a","th2":"b","th3":"c"}')
    compressor = StepCompressor(llm)
    long_traceback = TRACEBACK * 40
    traj = make_trajectory([make_event(0, long_traceback, EventType.TOOL_RESULT)])

    pool = compressor.compress(traj)

    assert llm.calls == []
    assert compressor.stats['skipped_machine'] == 1
    # Clipped, and the exception text at the end survived.
    assert len(pool[0]['th3']) <= 256
    assert pool[0]['th3'].endswith('ValueError: boom\n')


def test_prose_step_reaches_the_model():
    llm = RecordingLLM('{"th1":"detailed","th2":"moderate","th3":"terse"}')
    compressor = StepCompressor(llm)
    traj = make_trajectory([make_event(0, 'I will now consider the plan. ' * 60)])

    pool = compressor.compress(traj)

    assert len(llm.calls) == 1
    assert compressor.stats['llm_calls'] == 1
    assert pool[0] == {'th1': 'detailed', 'th2': 'moderate', 'th3': 'terse'}


def test_agent_step_is_not_short_circuited_by_the_machine_heuristic():
    """The clip path is for environment output, not for agent reasoning.

    An agent turn that happens to look structured is still cognition, and
    summarising it is the whole point of the stage.
    """
    llm = RecordingLLM('{"th1":"d","th2":"m","th3":"t"}')
    compressor = StepCompressor(llm)
    traj = make_trajectory([make_event(0, TRACEBACK * 40, EventType.AGENT_STEP)])

    compressor.compress(traj)

    assert len(llm.calls) == 1


def test_a_tier_over_its_cap_is_clipped_not_trusted():
    llm = RecordingLLM('{"th1":"' + 'z' * 4000 + '","th2":"m","th3":"t"}')
    compressor = StepCompressor(llm, th1_chars=100)
    traj = make_trajectory([make_event(0, 'prose ' * 200)])

    pool = compressor.compress(traj)

    assert len(pool[0]['th1']) <= 100


def test_unparseable_response_falls_back_to_clipping():
    llm = RecordingLLM('I am sorry, I cannot do that.')
    compressor = StepCompressor(llm)
    body = 'unique-head ' + ('m' * 3000) + ' unique-tail'
    traj = make_trajectory([make_event(0, body)])

    pool = compressor.compress(traj)

    assert compressor.stats['parse_failures'] == 1
    assert 'unique-head' in pool[0]['th1']
    assert pool[0]['th1'].endswith('unique-tail')


def test_compress_role_follows_the_event_type():
    assert compress_role_for(make_event(0, 'x', EventType.TOOL_RESULT)) == 'compress'
    assert compress_role_for(make_event(0, 'x', EventType.OBSERVATION)) == 'compress'
    assert compress_role_for(make_event(0, 'x', EventType.AGENT_STEP)) == 'preserve'


def test_looks_machine_generated_rejects_prose():
    assert not looks_machine_generated(
        'I think the next step is to open the drawer.\n'
        'That seems reasonable given the observation.\n'
        'So I will do that now.'
    )
    assert looks_machine_generated(TRACEBACK)


# -- tier routing --------------------------------------------------------

def test_tier_ladder_by_distance():
    assert select_tier(0) == 'th1'
    assert select_tier(2) == 'th1'
    assert select_tier(3) == 'th2'
    assert select_tier(5) == 'th2'
    assert select_tier(6) == 'th3'


def test_environment_steps_are_pinned_terse_regardless_of_distance():
    assert select_tier(0, 'compress') == 'th3'


# -- graded rendering ----------------------------------------------------

@pytest.fixture
def graded_pool() -> Dict[int, Dict[str, str]]:
    return {
        index: {
            'th1': f'STEP{index}-DETAILED',
            'th2': f'STEP{index}-MODERATE',
            'th3': f'STEP{index}-TERSE',
        }
        for index in range(12)
    }


def test_focus_neighbourhood_is_detailed_and_the_far_past_is_terse(graded_pool):
    events = [make_event(index, f'body {index}') for index in range(12)]

    rendered = render_history_for_focus(events, focus_position=10, pool=graded_pool)

    assert 'STEP9-DETAILED' in rendered      # distance 1
    assert 'STEP6-MODERATE' in rendered      # distance 4
    assert 'STEP0-TERSE' in rendered         # distance 10


def test_history_only_before_hides_the_future(graded_pool):
    events = [make_event(index, f'body {index}') for index in range(12)]

    rendered = render_history_for_focus(events, focus_position=5, pool=graded_pool)

    assert 'STEP4' in rendered
    assert 'STEP6' not in rendered
    assert 'STEP11' not in rendered


def test_the_cap_takes_detail_from_the_far_past_first(graded_pool):
    events = [make_event(index, f'body {index}') for index in range(12)]

    rendered = render_history_for_focus(
        events, focus_position=11, pool=graded_pool, overall_cap_chars=400,
    )

    # The step next to the focus keeps its detail; the oldest one does not.
    assert 'STEP10-DETAILED' in rendered
    assert 'STEP0-DETAILED' not in rendered
    assert 'STEP0-MODERATE' not in rendered


def test_the_header_carries_step_index_not_position(graded_pool):
    events = [make_event(index * 7, f'body {index}') for index in range(3)]
    pool = {index: graded_pool[index] for index in range(3)}

    rendered = render_history_for_focus(events, focus_position=2, pool=pool)

    assert 'step=0' in rendered
    assert 'step=7' in rendered


def test_a_missing_pool_entry_degrades_to_clipping_not_to_silence():
    events = [make_event(0, 'A' * 900), make_event(1, 'B' * 900)]

    rendered = render_history_for_focus(events, focus_position=1, pool={})

    assert 'AAAA' in rendered


# -- chunk rendering -----------------------------------------------------

def test_chunk_members_are_detailed_and_outsiders_are_terse(graded_pool):
    events = [make_event(index, f'body {index}') for index in range(12)]
    builder = GradedContextBuilder(graded_pool)

    rendered = builder.render_chunk(events, events[:4])

    assert 'STEP0-DETAILED' in rendered
    assert 'STEP3-DETAILED' in rendered
    assert 'STEP11-TERSE' in rendered
    assert 'STEP11-DETAILED' not in rendered


def test_builder_round_trips_through_a_dict(graded_pool):
    builder = GradedContextBuilder(graded_pool, overall_cap_chars=1234)

    restored = GradedContextBuilder.from_dict(builder.to_dict())

    assert restored.overall_cap_chars == 1234
    assert restored.pool[3]['th2'] == 'STEP3-MODERATE'


def test_event_text_labels_its_fields():
    event = AgentEvent(
        trace_id='t1', event_id='e', agent_name='a',
        event_type=EventType.TOOL_RESULT, step_index=0,
        input='ls -la', output='two files', error='none',
    )

    text = event_text(event)

    assert 'input: ls -la' in text
    assert 'output: two files' in text
    assert 'error: none' in text


def test_json_mode_is_requested_by_default_for_compression():
    """Compression asks the provider to constrain the response.

    The detectors default this off, because not every endpoint supports it.
    Compression defaults it on because a truncated object loses all three tiers
    at once and silently falls back to clipping -- which is the behaviour the
    stage exists to replace.
    """
    llm = RecordingLLM('{"th1":"d","th2":"m","th3":"t"}')
    traj = make_trajectory([make_event(0, 'prose ' * 200)])

    StepCompressor(llm).compress(traj)

    assert llm.calls[0]['kwargs']['response_format'] == {'type': 'json_object'}


def test_compression_prompt_pins_the_output_language():
    """SWE-Bench-Pro task prompts are in Chinese; ALFWorld's are in English.

    A summary translated out of the source language resolves against nothing
    when a detector tries to match its quotes back to the trajectory, so the
    tier text has to stay in whatever language the step was written in.
    """
    llm = RecordingLLM('{"th1":"d","th2":"m","th3":"t"}')
    traj = make_trajectory([make_event(0, 'prose ' * 200)])

    StepCompressor(llm).compress(traj)

    system = llm.calls[0]['messages'][0]['content']
    assert 'language of the step itself' in system
