"""Verification for evidence-grounded findings.

A detector that is asked to quote its sources will produce quotes whether or not
it saw anything. Requiring the quotes is the cheap half; checking that they
actually occur in the trajectory is the half that turns a finding from a label
into a claim a reader can falsify by string search.

The check is deliberately mechanical. :attr:`FailureFinding.wrong_content_quote`
must appear in the event the finding blames, and
:attr:`FailureFinding.reference_quote` must appear in an event that
:attr:`FailureFinding.conflict_with` permits -- ``CONTEXT`` may cite an
observation or tool result, ``SELF`` may cite the blamed event or the agent's
own earlier reasoning, and so on. Scoping the reference is what stops "quote
something that disagrees" from being satisfiable by quoting anything at all.

Whitespace is normalized before comparison. Models reflow and re-indent text
they are copying, and failing a finding because a newline became a space would
report formatting as fabrication. Nothing else is normalized: case, wording and
punctuation must match, so an invented quote still fails.

Adapted from the evidence-grounding requirement in TrajDebug
(THU-KEG/TrajDebug, MIT), where Stage B requires a verbatim pair.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    ConflictAxis,
    EventType,
    FailureFinding,
)

__all__ = [
    'verify_finding_quotes',
    'annotate_quote_verification',
    'quote_verification_summary',
]

_WS = re.compile(r'\s+')

#: Markers a renderer appends when it truncates an event for the prompt. A model
#: copying a span that runs to the end of what it was shown will copy the marker
#: too, and the marker is not in the source, so the quote would fail
#: verification for a reason the model had no way to avoid. Only a *trailing*
#: marker is stripped: everything before it must still match exactly, so this
#: forgives the truncation artifact without forgiving an invented quote.
_TRUNCATION_MARKERS = ('…', '...', '[truncated]', '[...]')

#: Which event types each conflict axis may draw its reference quote from.
#: TASK is handled separately, because the goal is not an event.
_REFERENCE_SCOPE = {
    ConflictAxis.CONTEXT: {
        EventType.OBSERVATION,
        EventType.TOOL_RESULT,
        EventType.ERROR,
        EventType.LLM_RESPONSE,
        EventType.MEMORY_READ,
    },
    ConflictAxis.ENV: {
        EventType.OBSERVATION,
        EventType.TOOL_RESULT,
        EventType.ERROR,
    },
    ConflictAxis.SELF: {
        EventType.AGENT_STEP,
        EventType.PLAN,
        EventType.REFLECTION,
        EventType.LLM_CALL,
    },
    ConflictAxis.TASK: {
        EventType.RUN_START,
        EventType.HUMAN_FEEDBACK,
    },
}


def _norm(text: str) -> str:
    return _WS.sub(' ', text).strip()


def _norm_quote(text: str) -> str:
    """Normalize a model-supplied quote before matching.

    Beyond whitespace, drops a trailing truncation marker. See
    :data:`_TRUNCATION_MARKERS` for why that is not a loophole.
    """

    normalized = _norm(text)
    changed = True
    while changed:
        changed = False
        for marker in _TRUNCATION_MARKERS:
            if normalized.endswith(marker):
                normalized = normalized[: -len(marker)].rstrip()
                changed = True
    return normalized


def _event_text(event: AgentEvent) -> str:
    """Every string an event carries, joined for substring search."""

    parts: List[str] = []
    for value in (event.input, event.output, event.error):
        if isinstance(value, str):
            parts.append(value)
        elif value is not None:
            parts.append(repr(value))
    return _norm(' '.join(parts))


def _blamed_event(
    finding: FailureFinding, events: Sequence[AgentEvent]
) -> Optional[AgentEvent]:
    """Resolve the event a finding points at, by id then by step index."""

    if finding.event_id:
        for event in events:
            if event.event_id == finding.event_id:
                return event
    if finding.step_index is not None:
        for event in events:
            if event.step_index == finding.step_index:
                return event
    return None


def _permitted_reference_events(
    finding: FailureFinding,
    events: Sequence[AgentEvent],
    blamed: Optional[AgentEvent],
) -> List[AgentEvent]:
    """Events the reference quote is allowed to come from.

    With no ``conflict_with`` the whole trajectory is in scope: an unscoped
    finding is weaker, but calling it unverified would punish detectors that
    predate the axis.
    """

    if finding.conflict_with is None:
        return list(events)

    allowed_types = _REFERENCE_SCOPE.get(finding.conflict_with, set())
    scoped = [e for e in events if e.event_type in allowed_types]

    if finding.conflict_with is ConflictAxis.SELF:
        # "Contradicts itself" includes the blamed step's own text, and only
        # the agent's *earlier* reasoning -- a later step cannot be what an
        # earlier one contradicted.
        if blamed is not None:
            cutoff = blamed.step_index
            if cutoff is not None:
                scoped = [
                    e for e in scoped
                    if e.step_index is not None and e.step_index <= cutoff
                ]
            if blamed not in scoped:
                scoped.append(blamed)
    return scoped


def verify_finding_quotes(
    finding: FailureFinding,
    trajectory: AgentTrajectory,
) -> Optional[bool]:
    """Return the value :attr:`FailureFinding.quote_verified` should carry.

    ``None`` when the finding carries no quotes -- it was never checked, which
    is not the same as passing. ``True`` when both quotes resolve. ``False``
    when at least one does not, meaning the detector produced text the
    trajectory does not support.
    """

    wrong = finding.wrong_content_quote
    reference = finding.reference_quote
    if not wrong and not reference:
        return None

    events = list(trajectory.events)
    blamed = _blamed_event(finding, events)

    if wrong:
        needle = _norm_quote(wrong)
        if not needle:
            return False
        # A quote of the blamed step must come from the blamed step. Falling
        # back to the whole trajectory here would let a detector "support" a
        # claim about step 7 with text from step 40.
        haystack = _event_text(blamed) if blamed is not None else ''
        if needle not in haystack:
            return False

    if reference:
        needle = _norm_quote(reference)
        if not needle:
            return False
        candidates = _permitted_reference_events(finding, events, blamed)
        haystacks = [_event_text(e) for e in candidates]
        if finding.conflict_with in (None, ConflictAxis.TASK) and trajectory.goal:
            haystacks.append(_norm(trajectory.goal))
        if not any(needle in h for h in haystacks):
            return False

    return True


def annotate_quote_verification(
    findings: Iterable[FailureFinding],
    trajectory: AgentTrajectory,
) -> List[FailureFinding]:
    """Set ``quote_verified`` on each finding in place, and return them."""

    annotated = list(findings)
    for finding in annotated:
        finding.quote_verified = verify_finding_quotes(finding, trajectory)
    return annotated


def quote_verification_summary(findings: Iterable[FailureFinding]) -> dict:
    """Counts by verification state, for reporting a detector's grounding rate.

    The ratio of ``verified`` to ``verified + unsupported`` is a measurable
    hallucination rate for a detector -- a number this library could not
    previously produce about its own output.
    """

    summary = {'verified': 0, 'unsupported': 0, 'unchecked': 0}
    for finding in findings:
        if finding.quote_verified is True:
            summary['verified'] += 1
        elif finding.quote_verified is False:
            summary['unsupported'] += 1
        else:
            summary['unchecked'] += 1
    return summary
