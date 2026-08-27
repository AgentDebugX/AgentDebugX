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

A model can only quote what it was shown, which matters once a detector renders
compressed history rather than raw events. Checking a summary-derived quote
against the raw trajectory fails it for a reason the model had no way to avoid:
measured on SWE-Bench-Pro, only 30.5% of compressed text appears verbatim in the
source, and 83% of all quotes were being discarded -- taking the finding, and
often the whole trajectory's answer, with them. Callers may therefore pass
``shown``, the text actually rendered per event, as a second permitted haystack.

That does not weaken the guarantee -- an invented quote is absent from the
summary too -- but it does weaken the *evidence*, because a summary can itself
be wrong. Findings record which haystack resolved them and the summary-only
count is reported separately rather than folded into one rate.

Adapted from the evidence-grounding requirement in TrajDebug
(THU-KEG/TrajDebug, MIT), where Stage B requires a verbatim pair.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional, Sequence

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
        # The task statement is frequently the first observation rather than
        # run.start: TrajDebug's format puts the issue description in the first
        # user message, which imports as an observation, while `goal` carries
        # only a short summary of it. A task-axis quote of the real instructions
        # has to be able to resolve against the event that actually holds them.
        EventType.OBSERVATION,
    },
}


_ESCAPES = (('\\n', '\n'), ('\\t', '\t'), ('\\"', '"'), ("\\'", "'"))


def _unescape(text: str) -> str:
    """Turn JSON-style escape sequences into the characters they encode.

    Trajectories store tool arguments and results as JSON-encoded strings, so a
    Python function in the source reads ``def f():\\\\n    '''doc`` -- a literal
    backslash and ``n``, two characters. The model reads that and, reasonably,
    writes back a real newline. Whitespace normalization does not bridge the
    gap because a literal backslash is not whitespace, so a perfectly copied
    quote was failing verification. Measured on SWE-Bench-Pro, 5,037 of 5,038
    agent steps carry such escapes; this was the single largest cause of quotes
    being rejected, and every one of them was the model getting it right.

    Both sides go through this, so a model that copies the escaped form
    verbatim still matches.
    """
    for seq, char in _ESCAPES:
        text = text.replace(seq, char)
    return text


def _norm(text: str) -> str:
    return _WS.sub(' ', _unescape(text)).strip()


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


def _similarity(needle: str, haystack: str) -> float:
    """How closely the best-matching window of ``haystack`` resembles ``needle``.

    Anchors on the longest common substring, then scores a window the size of
    the needle around it with :class:`difflib.SequenceMatcher`. 1.0 is an exact
    match; an invented quote shares only stray words and scores well under any
    sensible threshold. Cheap enough to run only after exact matching fails.
    """
    import difflib

    if not needle or not haystack:
        return 0.0
    matcher = difflib.SequenceMatcher(None, haystack, needle, autojunk=False)
    match = matcher.find_longest_match(0, len(haystack), 0, len(needle))
    if match.size == 0:
        return 0.0
    start = max(0, match.a - match.b)
    window = haystack[start:start + len(needle)]
    return difflib.SequenceMatcher(None, window, needle, autojunk=False).ratio()


def _best_similarity(needle: str, haystacks: Iterable[str]) -> float:
    return max((_similarity(needle, h) for h in haystacks if h), default=0.0)


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
    shown: Optional[Mapping[str, str]] = None,
    similarity_threshold: Optional[float] = None,
) -> Optional[bool]:
    """Return the value :attr:`FailureFinding.quote_verified` should carry.

    ``None`` when the finding carries no quotes -- it was never checked, which
    is not the same as passing. ``True`` when both quotes resolve. ``False``
    when at least one does not, meaning the detector produced text that neither
    the trajectory nor what it was shown supports.

    ``shown`` maps ``event_id`` to the text the detector actually rendered for
    that event. Omit it and the check is against the source alone, exactly as
    before. Supplying it never widens *which* events may be cited -- only what
    counts as that event's text.

    ``similarity_threshold`` (0-1), when set, accepts a quote whose best window
    in the *same permitted haystacks* scores at least that similar, after exact
    matching has failed on every haystack. Measured on SWE-Bench-Pro, 43% of
    quotes were still rejected after the escape and summary fixes, most of
    them mixed Chinese/English spans the model copies with small drift. The
    score is recorded in ``metadata['quote_similarity']`` and such findings
    are counted separately, so a report can say how much of its grounding is
    approximate. Left ``None`` -- the default -- nothing changes.
    """

    wrong = finding.wrong_content_quote
    reference = finding.reference_quote
    if not wrong and not reference:
        return None

    events = list(trajectory.events)
    blamed = _blamed_event(finding, events)
    via_source = True
    similarity = 1.0

    if wrong:
        needle = _norm_quote(wrong)
        if not needle:
            return False
        # A quote of the blamed step must come from the blamed step. Falling
        # back to the whole trajectory here would let a detector "support" a
        # claim about step 7 with text from step 40.
        source = _event_text(blamed) if blamed is not None else ''
        if needle not in source:
            rendered = (shown or {}).get(blamed.event_id) if blamed is not None else None
            if rendered and needle in _norm(rendered):
                via_source = False
            else:
                score = _best_similarity(needle, [source, _norm(rendered or '')])
                if similarity_threshold is None or score < similarity_threshold:
                    return False
                similarity = min(similarity, score)
                via_source = False

    if reference:
        needle = _norm_quote(reference)
        if not needle:
            return False
        candidates = _permitted_reference_events(finding, events, blamed)
        haystacks = [_event_text(e) for e in candidates]
        if finding.conflict_with in (None, ConflictAxis.TASK) and trajectory.goal:
            haystacks.append(_norm(trajectory.goal))
        if not any(needle in h for h in haystacks):
            widened = [
                _norm(shown[e.event_id])
                for e in candidates
                if shown and e.event_id in shown and shown[e.event_id]
            ]
            if not any(needle in h for h in widened):
                score = _best_similarity(needle, haystacks + widened)
                if similarity_threshold is None or score < similarity_threshold:
                    return False
                similarity = min(similarity, score)
            via_source = False

    if similarity < 1.0:
        finding.metadata['quote_verified_against'] = 'similar'
        finding.metadata['quote_similarity'] = round(similarity, 3)
    else:
        finding.metadata['quote_verified_against'] = 'source' if via_source else 'shown'
    return True


def annotate_quote_verification(
    findings: Iterable[FailureFinding],
    trajectory: AgentTrajectory,
    shown: Optional[Mapping[str, str]] = None,
    similarity_threshold: Optional[float] = None,
) -> List[FailureFinding]:
    """Set ``quote_verified`` on each finding in place, and return them."""

    annotated = list(findings)
    for finding in annotated:
        finding.quote_verified = verify_finding_quotes(
            finding, trajectory, shown, similarity_threshold
        )
    return annotated


def quote_verification_summary(findings: Iterable[FailureFinding]) -> dict:
    """Counts by verification state, for reporting a detector's grounding rate.

    The ratio of ``verified`` to ``verified + unsupported`` is a measurable
    hallucination rate for a detector -- a number this library could not
    previously produce about its own output.
    """

    summary = {'verified': 0, 'unsupported': 0, 'unchecked': 0,
               'verified_via_shown': 0, 'verified_via_similarity': 0}
    for finding in findings:
        if finding.quote_verified is True:
            summary['verified'] += 1
            # Counted separately because a quote that resolves only against the
            # rendered summary is weaker evidence than one found in the source:
            # the summary itself is model output and can be wrong.
            against = finding.metadata.get('quote_verified_against')
            if against == 'shown':
                summary['verified_via_shown'] += 1
            elif against == 'similar':
                summary['verified_via_similarity'] += 1
        elif finding.quote_verified is False:
            summary['unsupported'] += 1
        else:
            summary['unchecked'] += 1
    return summary
