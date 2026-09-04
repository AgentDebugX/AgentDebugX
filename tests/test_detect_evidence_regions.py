"""Contracts for region-typed quote location.

``verify_finding_quotes`` says whether a quote resolves. These functions say
where, and the tests that matter are the ones a boolean cannot express: the
rung a quote lands on, the event and field it came from, a span that slices
back to the same text, an anchor that names a real event but the wrong one,
and a verbatim quote taken from *after* the step it is offered as evidence
for.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from agentdebug.diagnose.detect.evidence import (
    ANCHOR_ELSEWHERE,
    ANCHOR_NOT_DECLARED,
    ANCHOR_RESOLVED,
    ANCHOR_UNKNOWN_EVENT,
    GROUNDING_RUNGS,
    RUNG_ANCHORED,
    RUNG_EXACT,
    RUNG_NORMALIZED,
    RUNG_UNRESOLVABLE,
    _norm,
    _norm_with_map,
    annotate_evidence_regions,
    grounds_trajectory,
    locate_quote,
    resolve_anchor,
    verify_finding_quotes,
)
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    ConflictAxis,
    EventType,
    FailureFinding,
    FailureMode,
)

MODE = FailureMode(
    mode_id='observation.misread',
    name='Observation misread',
    family='observation',
    description='The agent misread environment or tool output.',
)

PLAN = 'First I will survey the room, then take the book.'
DESK = 'On the desk 1, you see a cd 3, a cellphone 1, and a pen 1.'
CLAIM = 'The desk has a desklamp, so I will examine it.'
VERDICT = 'Task failed: the book was never taken.'
GOAL = 'Look at the book under the desklamp.'


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(trace_id='t1', goal=GOAL)
    traj.add_event(AgentEvent(
        event_id='evt_plan', trace_id='t1', agent_name='agent',
        event_type=EventType.PLAN, step_index=0, output=PLAN,
    ))
    traj.add_event(AgentEvent(
        event_id='evt_desk', trace_id='t1', agent_name='env',
        event_type=EventType.TOOL_RESULT, step_index=1, output=DESK,
    ))
    traj.add_event(AgentEvent(
        event_id='evt_claim', trace_id='t1', agent_name='agent',
        event_type=EventType.AGENT_STEP, step_index=2, output=CLAIM,
    ))
    traj.add_event(AgentEvent(
        event_id='evt_verdict', trace_id='t1', agent_name='env',
        event_type=EventType.OBSERVATION, step_index=3, output=VERDICT,
    ))
    return traj


def _finding(step: Optional[int] = 2, **kwargs: Any) -> FailureFinding:
    return FailureFinding(failure_mode=MODE, step_index=step, **kwargs)


class TestEveryRung:
    def test_exact_reports_event_field_and_a_span_that_slices_back(
        self, trajectory: AgentTrajectory
    ) -> None:
        loc = locate_quote(trajectory, 'you see a cd 3')
        assert loc.rung == RUNG_EXACT
        assert loc.region == 'event'
        assert (loc.event_id, loc.event_index, loc.step_index) == ('evt_desk', 1, 1)
        assert loc.event_type == 'tool.result', 'the role a consumer maps to observation/tool result'
        assert loc.field == 'output'
        assert loc.span is not None and DESK[loc.span[0]:loc.span[1]] == loc.text == 'you see a cd 3'
        assert loc.grounded
        assert loc.anchor_status == ANCHOR_NOT_DECLARED

    def test_normalized_forgives_reflow_and_the_renderer_frame(
        self, trajectory: AgentTrajectory
    ) -> None:
        """A model copying a rendered line copies its framing. That is ours, not content."""
        loc = locate_quote(
            trajectory,
            '[step 1] event_id=evt_desk type=tool.result agent=env output="you  see a\n cd 3"',
        )
        assert loc.rung == RUNG_NORMALIZED
        assert loc.event_id == 'evt_desk' and loc.field == 'output'
        assert loc.span is not None and DESK[loc.span[0]:loc.span[1]] == 'you see a cd 3'
        assert loc.anchor == 'evt_desk', 'the id in the frame is the declared anchor'
        assert loc.anchor_status == ANCHOR_RESOLVED
        assert loc.grounded

    def test_anchored_is_a_real_pointer_with_no_locatable_text(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The event exists; the text does not. A pointer locates, it does not ground."""
        loc = locate_quote(trajectory, 'evt_desk: the agent looked at the desk')
        assert loc.rung == RUNG_ANCHORED
        assert loc.region == 'event'
        assert loc.event_id == 'evt_desk' and loc.event_index == 1
        assert loc.span is None and loc.text is None
        assert loc.anchor_status == ANCHOR_RESOLVED
        assert not loc.grounded
        assert RUNG_ANCHORED not in GROUNDING_RUNGS

        pointer_only = locate_quote(trajectory, 'evt_desk')
        assert pointer_only.rung == RUNG_ANCHORED

    def test_unresolvable_when_nothing_places_it(self, trajectory: AgentTrajectory) -> None:
        loc = locate_quote(trajectory, 'the agent deleted the production database')
        assert loc.rung == RUNG_UNRESOLVABLE
        assert loc.region == 'none'
        assert loc.event_id is None and loc.span is None
        assert not loc.grounded

    @pytest.mark.parametrize('quote', ['', '   ', None])
    def test_an_empty_quote_is_unresolvable_not_an_error(
        self, trajectory: AgentTrajectory, quote: Any
    ) -> None:
        assert locate_quote(trajectory, quote).rung == RUNG_UNRESOLVABLE


class TestAnchors:
    def test_resolve_anchor_is_exact_match_only(self, trajectory: AgentTrajectory) -> None:
        event = resolve_anchor(trajectory, 'evt_desk')
        assert event is not None and event.output == DESK
        assert resolve_anchor(trajectory, 'evt_des') is None
        assert resolve_anchor(trajectory, 'EVT_DESK') is None
        assert resolve_anchor(trajectory, None) is None
        assert resolve_anchor(trajectory, '') is None

    def test_an_invented_id_is_reported_as_such(self, trajectory: AgentTrajectory) -> None:
        loc = locate_quote(trajectory, 'evt_zzz999: you see a cd 3')
        assert loc.rung == RUNG_EXACT, 'the text is real'
        assert loc.event_id == 'evt_desk'
        assert loc.anchor == 'evt_zzz999'
        assert loc.anchor_status == ANCHOR_UNKNOWN_EVENT

        nowhere = locate_quote(trajectory, 'evt_zzz999: anything at all')
        assert nowhere.rung == RUNG_UNRESOLVABLE
        assert nowhere.anchor_status == ANCHOR_UNKNOWN_EVENT

    def test_a_real_id_with_another_events_text_is_a_mislabelling(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Genuine evidence attached to the wrong event. Not a fabrication, and not resolved."""
        loc = locate_quote(trajectory, 'you see a cd 3', anchor='evt_claim')
        assert loc.rung == RUNG_EXACT
        assert loc.event_id == 'evt_desk'
        assert loc.anchor == 'evt_claim'
        assert loc.anchor_status == ANCHOR_ELSEWHERE

    def test_the_caller_anchor_wins_over_an_embedded_one(
        self, trajectory: AgentTrajectory
    ) -> None:
        loc = locate_quote(trajectory, 'evt_plan: you see a cd 3', anchor='evt_desk')
        assert loc.anchor == 'evt_desk'
        assert loc.anchor_status == ANCHOR_RESOLVED

    def test_the_anchor_event_is_searched_first(self) -> None:
        """Text present in two events is credited to the one the finding names."""
        traj = AgentTrajectory(trace_id='t2')
        for i, eid in enumerate(('evt_a', 'evt_b')):
            traj.add_event(AgentEvent(
                event_id=eid, trace_id='t2', event_type=EventType.OBSERVATION,
                step_index=i, output='carrier nimbus',
            ))
        assert locate_quote(traj, 'carrier nimbus').event_id == 'evt_a'
        assert locate_quote(traj, 'carrier nimbus', anchor='evt_b').event_id == 'evt_b'


class TestRegions:
    def test_the_goal_is_typed_not_rejected_and_grounds_nothing(
        self, trajectory: AgentTrajectory
    ) -> None:
        loc = locate_quote(trajectory, 'book under the desklamp')
        assert loc.rung == RUNG_EXACT, 'it is a faithful quote'
        assert loc.region == 'goal'
        assert loc.event_id is None
        assert loc.span is not None and GOAL[loc.span[0]:loc.span[1]] == loc.text
        assert not loc.grounded, 'faithful, and not evidence about what the agent did'

    def test_an_event_wins_a_tie_with_the_goal(self, trajectory: AgentTrajectory) -> None:
        traj = AgentTrajectory(trace_id='t3', goal=DESK, events=list(trajectory.events))
        assert locate_quote(traj, 'you see a cd 3').region == 'event'

    def test_shown_is_a_permitted_weaker_region(self, trajectory: AgentTrajectory) -> None:
        """A quote of the rendered summary resolves, and says so."""
        shown = {'evt_desk': 'Summary: the desk holds a cd, a phone and a pen.'}
        loc = locate_quote(trajectory, 'holds a cd, a phone', shown=shown)
        assert loc.region == 'shown'
        assert loc.event_id == 'evt_desk' and loc.field is None
        assert loc.span is not None
        assert shown['evt_desk'][loc.span[0]:loc.span[1]] == 'holds a cd, a phone'
        assert loc.grounded

    def test_input_and_error_fields_are_searched(self) -> None:
        traj = AgentTrajectory(trace_id='t4')
        traj.add_event(AgentEvent(
            event_id='evt_call', trace_id='t4', event_type=EventType.TOOL_CALL,
            step_index=0, input={'tool': 'open', 'target': 'fridge 1'},
            error='PermissionError: fridge 1 is locked',
        ))
        by_input = locate_quote(traj, "'target': 'fridge 1'")
        assert (by_input.rung, by_input.field) == (RUNG_EXACT, 'input')
        by_error = locate_quote(traj, 'fridge 1 is locked')
        assert (by_error.rung, by_error.field) == (RUNG_EXACT, 'error')


class TestJsonEscapedSource:
    """The case upstream commit 3438845 fixed for verification must locate too."""

    ESCAPED = json.dumps({'content': "def f():\n    '''doc'''\n    return 1"})

    def _traj(self) -> AgentTrajectory:
        traj = AgentTrajectory(trace_id='t5')
        traj.add_event(AgentEvent(
            event_id='evt_src', trace_id='t5', event_type=EventType.TOOL_RESULT,
            step_index=0, output=self.ESCAPED,
        ))
        return traj

    def test_a_quote_with_real_newlines_locates_in_the_escaped_field(self) -> None:
        traj = self._traj()
        assert '\\n' in self.ESCAPED and '\n' not in self.ESCAPED
        loc = locate_quote(traj, "def f():\n    '''doc'''")
        assert loc.rung == RUNG_NORMALIZED
        assert loc.field == 'output'
        assert loc.span is not None
        # The span is into the RAW escaped field, and covers the escape sequences.
        assert self.ESCAPED[loc.span[0]:loc.span[1]] == loc.text
        assert loc.text is not None and loc.text.startswith('def f():\\n')
        assert _norm(loc.text) == _norm("def f():\n    '''doc'''")

    def test_location_and_verification_agree(self) -> None:
        traj = self._traj()
        finding = FailureFinding(
            failure_mode=MODE, event_id='evt_src', step_index=0,
            wrong_content_quote="def f():\n    '''doc'''",
            reference_quote='return 1', conflict_with=ConflictAxis.SELF,
        )
        assert verify_finding_quotes(finding, traj) is True
        [grounding] = grounds_trajectory([finding], traj)
        assert grounding.grounded is True


class TestNormalizationMap:
    @pytest.mark.parametrize('text', [
        'plain',
        '  padded\t\ttext \n',
        'a\\nb\\tc\\"d\\\'e',
        'double\\\\n escape',
        'nbsp here and em',
        '',
        '   ',
        'trailing escape\\',
    ])
    def test_the_map_reproduces_norm_exactly(self, text: str) -> None:
        """Location and verification must agree, so the mapped text IS ``_norm``."""
        mapped, spans = _norm_with_map(text)
        assert mapped == _norm(text)
        assert len(spans) == len(mapped)
        for (start, end), char in zip(spans, mapped):
            assert 0 <= start < end <= len(text)
            if char != ' ':
                assert _norm(text[start:end]) == char


class TestGroundsTrajectory:
    def test_evidence_after_the_blamed_step_is_hindsight(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Verbatim, and still not evidence: the verdict came after the act."""
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='the book was never taken',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is True, 'the boolean cannot see this'
        [g] = grounds_trajectory([finding], trajectory)
        wrong, reference = g.quotes
        assert (wrong.role, wrong.position, wrong.at_or_before_blame) == ('wrong_content', 'at', True)
        assert wrong.location.anchor_status == ANCHOR_RESOLVED
        assert (reference.role, reference.position) == ('reference', 'after')
        assert reference.at_or_before_blame is False
        assert reference.location.grounded, 'the quote itself is real'
        assert g.grounded is False

    def test_evidence_before_the_blamed_step_grounds(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        [g] = grounds_trajectory([finding], trajectory)
        assert (g.blamed_event_id, g.blamed_event_index, g.blamed_step_index) == ('evt_claim', 2, 2)
        assert [q.position for q in g.quotes] == ['at', 'before']
        assert g.grounded is True

    def test_a_wrong_content_quote_from_another_step_does_not_ground(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='you see a cd 3',
            reference_quote='survey the room',
            conflict_with=ConflictAxis.SELF,
        )
        [g] = grounds_trajectory([finding], trajectory)
        wrong = g.quotes[0]
        assert wrong.location.rung == RUNG_EXACT
        assert wrong.location.anchor_status == ANCHOR_ELSEWHERE
        assert wrong.position == 'before'
        assert g.grounded is False

    def test_no_quotes_is_none_like_quote_verified(self, trajectory: AgentTrajectory) -> None:
        [g] = grounds_trajectory([_finding()], trajectory)
        assert g.quotes == ()
        assert g.grounded is None

    def test_an_unresolvable_quote_has_no_position(self, trajectory: AgentTrajectory) -> None:
        finding = _finding(wrong_content_quote='never happened', reference_quote='nor this')
        [g] = grounds_trajectory([finding], trajectory)
        assert [q.location.rung for q in g.quotes] == [RUNG_ANCHORED, RUNG_UNRESOLVABLE]
        assert g.quotes[0].position == 'at', 'the pointer places it; the text does not ground it'
        assert g.quotes[1].position is None
        assert g.quotes[1].at_or_before_blame is None
        assert g.grounded is False

    def test_a_finding_blaming_a_step_the_trajectory_lacks(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(step=9, wrong_content_quote='you see a cd 3')
        [g] = grounds_trajectory([finding], trajectory)
        assert g.blamed_event_id is None
        assert g.quotes[0].location.rung == RUNG_EXACT
        assert g.quotes[0].position is None
        assert g.grounded is False

    def test_annotation_lands_next_to_quote_verified_and_is_json(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        annotate_evidence_regions([finding], trajectory)
        payload = finding.metadata['evidence_regions']
        json.dumps(payload)
        assert payload['grounded'] is True
        assert payload['quotes'][1]['location']['event_id'] == 'evt_desk'
        assert payload['quotes'][1]['location']['span'] == [15, 29]
        assert payload['quotes'][1]['position'] == 'before'

    def test_shown_is_threaded_through(self, trajectory: AgentTrajectory) -> None:
        shown = {'evt_desk': 'Summary: the desk holds a cd, a phone and a pen.'}
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='holds a cd, a phone',
            conflict_with=ConflictAxis.CONTEXT,
        )
        [g] = grounds_trajectory([finding], trajectory, shown)
        assert g.quotes[1].location.region == 'shown'
        assert g.grounded is True
