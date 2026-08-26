"""Contracts for evidence-grounded finding verification.

The point of these fields is that a finding becomes falsifiable by string
search. So the tests that matter are the negative ones: a fabricated quote, a
quote lifted from the wrong step, and a quote that cites a source its conflict
axis does not permit must all fail. If those pass, the mechanism is decorative.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from agentdebug.diagnose.detect.evidence import (
    annotate_quote_verification,
    quote_verification_summary,
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

DESK = 'On the desk 1, you see a cd 3, a cellphone 1, and a pen 1.'
CLAIM = 'The desk has a desklamp, so I will examine it.'
PLAN = 'First I will survey the room, then take the book.'


@pytest.fixture
def trajectory() -> AgentTrajectory:
    traj = AgentTrajectory(trace_id='t1', goal='Look at the book under the desklamp.')
    traj.add_event(AgentEvent(
        trace_id='t1', agent_name='agent', event_type=EventType.PLAN,
        step_index=0, output=PLAN,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', agent_name='env', event_type=EventType.TOOL_RESULT,
        step_index=1, output=DESK,
    ))
    traj.add_event(AgentEvent(
        trace_id='t1', agent_name='agent', event_type=EventType.AGENT_STEP,
        step_index=2, output=CLAIM,
    ))
    return traj


def _finding(step: Optional[int] = 2, **kwargs: Any) -> FailureFinding:
    return FailureFinding(failure_mode=MODE, step_index=step, **kwargs)


class TestGroundedFindingsPass:
    def test_both_quotes_resolve(self, trajectory: AgentTrajectory) -> None:
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3, a cellphone 1, and a pen 1',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is True

    def test_reflowed_whitespace_is_forgiven(self, trajectory: AgentTrajectory) -> None:
        """Models re-indent text they copy; that is formatting, not fabrication."""

        finding = _finding(
            wrong_content_quote='The desk    has a\n   desklamp',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is True

    @pytest.mark.parametrize('marker', ['…', '...', '[truncated]'])
    def test_a_trailing_truncation_marker_is_stripped(
        self, trajectory: AgentTrajectory, marker: str
    ) -> None:
        """Caught on real data, and it was our bug rather than the model's.

        The prompt renderer truncates long events and appends a marker. A model
        copying a span that runs to the end of what it was shown copies the
        marker too. Rejecting that would discard correct findings for a reason
        the model could not have avoided -- and did, on the first live run.

        Only a trailing marker is dropped; everything before it must still
        match exactly, so an invented quote cannot be laundered by suffixing
        an ellipsis.
        """

        finding = _finding(
            wrong_content_quote='The desk has a desklamp' + marker,
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is True

    def test_a_marker_does_not_launder_an_invented_quote(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='I never said this…',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_task_axis_may_cite_the_goal(self, trajectory: AgentTrajectory) -> None:
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='book under the desklamp',
            conflict_with=ConflictAxis.TASK,
        )
        assert verify_finding_quotes(finding, trajectory) is True

    def test_self_axis_may_cite_the_agents_earlier_plan(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='First I will survey the room',
            conflict_with=ConflictAxis.SELF,
        )
        assert verify_finding_quotes(finding, trajectory) is True

    def test_an_unscoped_finding_may_cite_anywhere(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Detectors predating the axis should not be marked unsupported."""

        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3',
            conflict_with=None,
        )
        assert verify_finding_quotes(finding, trajectory) is True


class TestUngroundedFindingsFail:
    def test_a_fabricated_reference_is_rejected(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The whole reason the fields exist.

        The desk listing never mentions a desklamp. A detector claiming it did
        has produced text the trajectory does not support.
        """

        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a desklamp 1',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_a_fabricated_wrong_quote_is_rejected(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            wrong_content_quote='I will ignore the desk entirely',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_the_wrong_quote_must_come_from_the_blamed_step(
        self, trajectory: AgentTrajectory
    ) -> None:
        """Otherwise a claim about step 2 can be 'supported' by step 1's text."""

        finding = _finding(
            step=2,
            wrong_content_quote='you see a cd 3',   # this is step 1, not step 2
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_the_reference_must_respect_the_conflict_axis(
        self, trajectory: AgentTrajectory
    ) -> None:
        """SELF means the agent contradicted itself, not the environment.

        Without scoping, "quote something that disagrees" is satisfiable by
        quoting anything at all, and the axis carries no information.
        """

        finding = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3',      # a tool result, not the agent
            conflict_with=ConflictAxis.SELF,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_self_axis_will_not_cite_a_later_step(self) -> None:
        """A later step cannot be what an earlier one contradicted."""

        traj = AgentTrajectory(trace_id='t2', goal='g')
        traj.add_event(AgentEvent(
            trace_id='t2', agent_name='a', event_type=EventType.AGENT_STEP,
            step_index=0, output='I will take the blue key.',
        ))
        traj.add_event(AgentEvent(
            trace_id='t2', agent_name='a', event_type=EventType.AGENT_STEP,
            step_index=1, output='Actually the key is red.',
        ))
        finding = _finding(
            step=0,
            wrong_content_quote='I will take the blue key',
            reference_quote='Actually the key is red',
            conflict_with=ConflictAxis.SELF,
        )
        assert verify_finding_quotes(finding, traj) is False

    def test_an_empty_quote_is_not_a_pass(self, trajectory: AgentTrajectory) -> None:
        finding = _finding(
            wrong_content_quote='   ',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False

    def test_a_finding_pointing_at_no_real_event_fails(
        self, trajectory: AgentTrajectory
    ) -> None:
        finding = _finding(
            step=99,
            wrong_content_quote='The desk has a desklamp',
            conflict_with=ConflictAxis.CONTEXT,
        )
        assert verify_finding_quotes(finding, trajectory) is False


class TestUncheckedIsNotVerified:
    def test_a_finding_with_no_quotes_returns_none(
        self, trajectory: AgentTrajectory
    ) -> None:
        """None means never checked. A consumer must not read it as True."""

        assert verify_finding_quotes(_finding(), trajectory) is None

    def test_rule_based_findings_survive_untouched(
        self, trajectory: AgentTrajectory
    ) -> None:
        """The deterministic rule packs have no claim to quote.

        Marking them unsupported would make the zero-LLM path look like it was
        hallucinating, when it simply does not participate in this mechanism.
        """

        finding = _finding(evidence=['format/schema signal in event payload'])
        assert verify_finding_quotes(finding, trajectory) is None


class TestAnnotationAndSummary:
    def test_annotate_sets_the_field_in_place(
        self, trajectory: AgentTrajectory
    ) -> None:
        good = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='you see a cd 3',
            conflict_with=ConflictAxis.CONTEXT,
        )
        bad = _finding(
            wrong_content_quote='The desk has a desklamp',
            reference_quote='a desklamp is present',
            conflict_with=ConflictAxis.CONTEXT,
        )
        bare = _finding()

        annotate_quote_verification([good, bad, bare], trajectory)

        assert good.quote_verified is True
        assert bad.quote_verified is False
        assert bare.quote_verified is None

    def test_summary_separates_the_three_states(
        self, trajectory: AgentTrajectory
    ) -> None:
        findings = [
            _finding(
                wrong_content_quote='The desk has a desklamp',
                reference_quote='you see a cd 3',
                conflict_with=ConflictAxis.CONTEXT,
            ),
            _finding(
                wrong_content_quote='The desk has a desklamp',
                reference_quote='invented text',
                conflict_with=ConflictAxis.CONTEXT,
            ),
            _finding(),
        ]
        annotate_quote_verification(findings, trajectory)

        assert quote_verification_summary(findings) == {
            'verified': 1,
            'unsupported': 1,
            'unchecked': 1,
        }
