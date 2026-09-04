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
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

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
    'QuoteLocation',
    'QuoteGrounding',
    'FindingGrounding',
    'locate_quote',
    'resolve_anchor',
    'grounds_trajectory',
    'annotate_evidence_regions',
    'GROUNDING_RUNGS',
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


def quote_verification_summary(findings: Iterable[FailureFinding]) -> Dict[str, int]:
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


# ---------------------------------------------------------------------------
# Region-typed location
#
# :func:`verify_finding_quotes` answers "does the quote occur where the finding
# says it does" with a boolean, which is what a detector needs to keep or drop
# a finding. A consumer that *stores* findings needs more than the boolean. It
# needs to know where the quote was found -- which event, which field of it,
# at which character span -- so that a claim about the trajectory can be
# checked against the position of its evidence: a verbatim quote of the
# grader's verdict at the last event does not support a diagnosis of step 4.
# And when the quote cannot be found it needs to know whether the finding at
# least pointed at a real event, because an invented event id and a paraphrase
# of a real event are different defects with different fixes.
#
# Nothing below changes what :func:`verify_finding_quotes` accepts. The
# ``normalized`` rung applies the same normalisation first, so a quote that
# verifies also locates; the extra information is *where*. It then goes one
# step wider than verification -- quotation marks are dropped on both sides
# (see :func:`_canon`) -- so a quote may locate without verifying. Measured on
# 1,500 evidence quotes from a consumer's debug corpus (AgentErrorData,
# ``scripts/evidence_region_vs_upstream.py``), 402 quotes the consumer's own
# rung ladder anchored were not grounded here; 337 of them differed from the
# stored field in nothing but a renderer's role label (``Observation: ...``,
# ``step 8 action: ...``) and 11 more in nothing but the kind of quotation
# mark, which is why both are treated as framing rather than content. After
# this change 54 remain, and those are real edits or two-field composites.
# ---------------------------------------------------------------------------

#: Rungs, strictest first. A quote is credited to the first rung that locates it.
#: ``exact`` -- verbatim substring of the stored field.
#: ``normalized`` -- substring after the normalisation ``verify_finding_quotes``
#: applies (JSON escapes, whitespace, a trailing truncation marker) and after
#: removing the framing a renderer puts around an event -- ``[step 3]``,
#: ``event_id=evt_...``, ``output=``, ``Observation:`` -- which a model copying
#: a line copies too; failing that, with quotation marks dropped on both
#: sides, since a stored ``repr`` and a model's JSON re-quoting of it differ
#: in nothing else.
#: ``anchored`` -- the text was found nowhere, but the quote (or the caller)
#: named an event id that exists. A pointer, not a transcription.
#: ``unresolvable`` -- neither the text nor an anchor places it.
RUNG_EXACT = 'exact'
RUNG_NORMALIZED = 'normalized'
RUNG_ANCHORED = 'anchored'
RUNG_UNRESOLVABLE = 'unresolvable'
#: Rungs under which the quoted text is demonstrably present. ``anchored`` is
#: deliberately excluded: a real pointer with unverifiable text locates an
#: event, but it does not let a reader falsify the quote by string search.
GROUNDING_RUNGS = frozenset({RUNG_EXACT, RUNG_NORMALIZED})

#: Where a quote was found. ``event`` is a stored field of an event; ``shown``
#: is the text a detector rendered for an event (see ``verify_finding_quotes``
#: for why that is a permitted, weaker, haystack); ``goal`` is the task
#: statement, which is not an event and grounds nothing about what the agent
#: did.
REGION_EVENT = 'event'
REGION_SHOWN = 'shown'
REGION_GOAL = 'goal'
REGION_NONE = 'none'

#: What the declared anchor turned out to be, relative to where the text was
#: found. ``resolved`` and ``elsewhere`` both mean the event exists; they
#: differ in whether the quote is in it. A real id carrying another event's
#: text is a mislabelling, not a fabrication, and the two need different fixes.
ANCHOR_NOT_DECLARED = 'not_declared'
ANCHOR_RESOLVED = 'resolved'
ANCHOR_UNKNOWN_EVENT = 'unknown_event'
ANCHOR_ELSEWHERE = 'elsewhere'

_EVENT_FIELDS = ('input', 'output', 'error')

#: The label a renderer prints in front of an event's text (``Thought:``,
#: ``Action:``, ``Observation:``, ``Feedback:``), and the ones models add
#: themselves when reproducing a line (``env:``, ``error:``, ``solver:``,
#: ``verifier signal:``). A renderer that folds the error field into the
#: label -- ``Observation (tool error: unknown_carrier): Rejected.`` -- is
#: covered by the optional parenthetical. The label is framing; the text
#: after the colon must still match a stored field.
_ROLE_LABEL = (
    r'(?:thought|action|observation|feedback|env|error|solver|signal|user|executor'
    r'|verifier[ _]signal|verification|verifier)(?:\s*\([^)]*\))?\s*:'
)
#: The event-id forms the built-in renderers print (``event_id=evt_x``) and
#: the ones models write when asked to cite (``evt_x: ...``, ``(evt_x) ...``,
#: ``[evt_x][agent][kind] ...``).
_EVENT_REF = (
    r'\[(?P<bracket>evt_\w+)\](?:\[[^\]]*\]){0,2}'
    r'|\((?P<paren>evt_\w+)\)'
    r'|event_id\s*=\s*(?P<kw>[^\s,;:]+)'
    r'|(?P<colon>evt_\w+)\s*[:\-]'
)
#: Framing a renderer puts around an event, or a model adds when reproducing a
#: line. Anchored on the start of the quote, so only a leading frame is
#: removed and the quoted content itself must still match. A bare ``step 3``
#: (no colon) counts as framing only when an event reference or a role label
#: follows it, so ``step 3 of the plan`` keeps its first two words.
_FRAME = re.compile(
    r'^\s*'
    r'(?:\[step\s+\d+\]|step\s+\d+\s*[:\-]'
    r'|step\s+\d+(?=\s+(?:[\[(]?evt_|event_id\s*=|' + _ROLE_LABEL + r')))?\s*'
    r'(?:' + _EVENT_REF + r')?\s*'
    r'(?:(?:type|agent)=\S+\s*)*'
    r'(?:' + _ROLE_LABEL + r')?\s*'
    r'(?:(?:input|output|error|metadata)\s*[:=])?\s*',
    re.IGNORECASE,
)
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ('`', '`'), ('“', '”'), ('‘', '’'))


_BARE_ID = re.compile(r'^(?P<id>evt_[A-Za-z0-9_-]+)(?:\s*:\s*(?P<rest>.*))?$', re.S)


def _split_frame(quote: str) -> Tuple[Optional[str], str]:
    """Return ``(declared event id, content)`` for a possibly framed quote.

    One pair of wrapping quotation marks is removed as well, because a model
    that writes ``step 3: "text"`` has quoted ``text``. Only a matching pair
    is removed, so a quote that legitimately begins with a quotation mark --
    a JSON fragment such as ``"answer": 5`` -- keeps it.
    """
    match = _FRAME.match(quote)
    declared: Optional[str] = None
    inner = quote
    if match:
        declared = (
            match.group('bracket') or match.group('paren')
            or match.group('kw') or match.group('colon')
        )
        inner = quote[match.end():]
    if declared is None:
        # `_FRAME` is entirely optional and so always matches; a bare event id
        # (`evt_x`) or `evt_x: text` that its alternatives do not cover is
        # still a declared anchor.
        bare = _BARE_ID.match(inner.strip())
        if bare:
            declared = bare.group('id')
            inner = bare.group('rest') or ''
    inner = inner.strip()
    for open_, close in _QUOTE_PAIRS:
        if len(inner) >= 2 and inner.startswith(open_) and inner.endswith(close):
            inner = inner[1:-1].strip()
            break
    return declared, inner


def _norm_with_map(text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """:func:`_norm`, plus for each output character its ``(start, end)`` in ``text``.

    Kept step-for-step equivalent to ``_norm`` -- the same sequential escape
    replacements, then whitespace runs collapsed to one space, then a strip --
    so that a needle found in the mapped text is found by ``_norm`` too. The
    map is what lets a match made after normalisation report a span into the
    raw field, which is the only form a span is useful in: a consumer slices
    the stored value, not our scratch string.
    """
    chars = list(text)
    spans = [(i, i + 1) for i in range(len(text))]
    for seq, char in _ESCAPES:
        chars, spans = _replace_with_map(chars, spans, seq, char)
    return _collapse_ws_with_map(chars, spans)


def _collapse_ws_with_map(
    chars: Sequence[str], spans: Sequence[Tuple[int, int]]
) -> Tuple[str, List[Tuple[int, int]]]:
    """``_WS.sub(' ', text).strip()`` over a character list, spans carried along."""
    out_chars: List[str] = []
    out_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(chars):
        if chars[i].isspace():
            j = i
            while j < len(chars) and chars[j].isspace():
                j += 1
            out_chars.append(' ')
            out_spans.append((spans[i][0], spans[j - 1][1]))
            i = j
        else:
            out_chars.append(chars[i])
            out_spans.append(spans[i])
            i += 1
    while out_chars and out_chars[0] == ' ':
        out_chars.pop(0)
        out_spans.pop(0)
    while out_chars and out_chars[-1] == ' ':
        out_chars.pop()
        out_spans.pop()
    return ''.join(out_chars), out_spans


#: Quotation marks, straight and typographic. A stored tool call is a Python
#: ``repr`` with single quotes; a model re-quotes it as JSON with double
#: quotes, or a chat surface curls them. Measured on the consumer corpus
#: above, 11 quotes located only once these were dropped. Case, wording and
#: punctuation are still not normalised.
_QUOTE_MARKS = '"\'`\u2018\u2019\u201c\u201d'
_QUOTE_CHARS = re.compile('[' + re.escape(_QUOTE_MARKS) + ']')


def _canon(text: str) -> str:
    """:func:`_norm`, then quotation marks dropped and whitespace re-collapsed.

    Dropping a mark can leave two spaces adjacent (``a ' b``) or one at an
    end (``'x'``), so the collapse runs again; both sides go through the same
    function, so what matters is only that it is deterministic.
    """
    return _WS.sub(' ', _QUOTE_CHARS.sub('', _norm(text))).strip()


def _canon_quote(text: str) -> str:
    """:func:`_canon` of a model-supplied quote, truncation marker removed first."""
    return _canon(_norm_quote(text))


def _canon_with_map(text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """:func:`_canon`, plus the offset map :func:`_norm_with_map` provides.

    Built on the ``_norm`` map, so the two stay equivalent by construction:
    the mapped characters are filtered and re-collapsed exactly as ``_canon``
    filters and re-collapses the string.
    """
    mapped, spans = _norm_with_map(text)
    kept = [(c, span) for c, span in zip(mapped, spans) if c not in _QUOTE_MARKS]
    return _collapse_ws_with_map([c for c, _ in kept], [span for _, span in kept])


def _replace_with_map(
    chars: List[str], spans: List[Tuple[int, int]], seq: str, char: str
) -> Tuple[List[str], List[Tuple[int, int]]]:
    """``str.replace`` over a character list, carrying source spans along."""
    n = len(seq)
    out_chars: List[str] = []
    out_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(chars):
        if chars[i] == seq[0] and ''.join(chars[i:i + n]) == seq:
            out_chars.append(char)
            out_spans.append((spans[i][0], spans[i + n - 1][1]))
            i += n
        else:
            out_chars.append(chars[i])
            out_spans.append(spans[i])
            i += 1
    return out_chars, out_spans


def _field_text(event: AgentEvent, field: str) -> str:
    """The string a field is searched as. Mirrors ``_event_text``: non-strings by repr."""
    value = getattr(event, field, None)
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return repr(value)


def _event_type_value(event: AgentEvent) -> str:
    return str(getattr(event.event_type, 'value', event.event_type))


@dataclass(frozen=True)
class QuoteLocation:
    """Where a quote was found, and how.

    ``span`` indexes the text named by ``region`` and ``field``: the stored
    field (as ``str``, or ``repr`` for a non-string value), the ``shown`` text
    for that event, or the goal. ``text`` is that slice, so a reader can
    confirm the span without recomputing anything. Both are ``None`` for the
    ``anchored`` and ``unresolvable`` rungs, where no text was located.
    """

    rung: str
    region: str
    event_id: Optional[str] = None
    event_index: Optional[int] = None
    step_index: Optional[int] = None
    event_type: Optional[str] = None
    field: Optional[str] = None
    span: Optional[Tuple[int, int]] = None
    text: Optional[str] = None
    anchor: Optional[str] = None
    anchor_status: str = ANCHOR_NOT_DECLARED

    @property
    def grounded(self) -> bool:
        """Is the text demonstrably present in something the agent produced or saw?

        Both halves matter. A quote located in the goal is faithful and
        grounds nothing about the trajectory; a real pointer with text found
        nowhere locates an event but cannot be checked by string search.
        """
        return self.rung in GROUNDING_RUNGS and self.region in (REGION_EVENT, REGION_SHOWN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'rung': self.rung,
            'region': self.region,
            'event_id': self.event_id,
            'event_index': self.event_index,
            'step_index': self.step_index,
            'event_type': self.event_type,
            'field': self.field,
            'span': list(self.span) if self.span is not None else None,
            'text': self.text,
            'anchor': self.anchor,
            'anchor_status': self.anchor_status,
            'grounded': self.grounded,
        }


def resolve_anchor(trajectory: AgentTrajectory, event_id: Optional[str]) -> Optional[AgentEvent]:
    """The event an id names, or ``None``.

    Exact match only. Ids come from the trajectory, and a detector is asked to
    copy them; an id that differs in any character is one the detector did
    not copy, which is the case this exists to expose.
    """
    if not event_id:
        return None
    for event in trajectory.events:
        if event.event_id == event_id:
            return event
    return None


def locate_quote(
    trajectory: AgentTrajectory,
    quote: str,
    *,
    anchor: Optional[str] = None,
    shown: Optional[Mapping[str, str]] = None,
) -> QuoteLocation:
    """Find where ``quote`` occurs in what the detector was given.

    Rungs are tried strictest first, and within a rung the anchor's event is
    searched before the others, then the remaining events in trajectory
    order, then the goal. So a quote present in both its anchor and a later
    event is credited to the anchor, and one present in both an event and the
    goal is credited to the event.

    ``anchor`` is the event id the caller says the quote came from -- for a
    ``wrong_content_quote`` that is the blamed event. When the caller passes
    none, an id embedded in the quote itself (``evt_x: ...``) is used. Either
    way the outcome is reported in ``anchor_status`` rather than folded into
    the rung, because "the text is real but the id is wrong" is a different
    finding from "the text is invented".

    ``shown`` maps event id to the text a detector rendered for that event,
    exactly as in :func:`verify_finding_quotes`; a quote found only there is
    reported with ``region='shown'``.
    """
    events = list(trajectory.events)
    declared, inner = _split_frame(quote or '')
    anchor_id = anchor or declared
    anchor_event = resolve_anchor(trajectory, anchor_id)
    if anchor_id and anchor_event is None:
        anchor_state = ANCHOR_UNKNOWN_EVENT
    elif anchor_id:
        anchor_state = ANCHOR_RESOLVED
    else:
        anchor_state = ANCHOR_NOT_DECLARED

    def finish(loc: QuoteLocation) -> QuoteLocation:
        status = anchor_state
        if anchor_event is not None and loc.event_id != anchor_event.event_id:
            status = ANCHOR_ELSEWHERE
        return QuoteLocation(
            rung=loc.rung, region=loc.region, event_id=loc.event_id,
            event_index=loc.event_index, step_index=loc.step_index,
            event_type=loc.event_type, field=loc.field, span=loc.span,
            text=loc.text, anchor=anchor_id, anchor_status=status,
        )

    if not (quote or '').strip():
        return finish(QuoteLocation(rung=RUNG_UNRESOLVABLE, region=REGION_NONE))

    order = list(range(len(events)))
    if anchor_event is not None:
        order.sort(key=lambda i: events[i] is not anchor_event)

    # Rung 1: the quote as given, verbatim, then with its own framing removed.
    # Rung 2: after the normalisation `verify_finding_quotes` applies, then --
    # wider than verification -- with quotation marks dropped. At every step
    # the framed form is tried before the unframed one, so that a quote copied
    # from a rendering that includes the frame still resolves against `shown`.
    for rung, prepare, finder in _LADDER:
        needles: List[str] = []
        for candidate in (prepare(quote), prepare(inner)):
            if candidate and candidate not in needles:
                needles.append(candidate)
        for needle in needles:
            for i in order:
                found = _find_in_event(events[i], i, needle, shown, rung, finder)
                if found is not None:
                    return finish(found)
            if trajectory.goal:
                span = finder(needle, trajectory.goal)
                if span is not None:
                    return finish(QuoteLocation(
                        rung=rung, region=REGION_GOAL, span=span,
                        text=trajectory.goal[span[0]:span[1]],
                    ))

    # Rung 3: no text located; a real anchor still places the claim at an event.
    if anchor_event is not None:
        index = events.index(anchor_event)
        return finish(QuoteLocation(
            rung=RUNG_ANCHORED, region=REGION_EVENT, event_id=anchor_event.event_id,
            event_index=index, step_index=anchor_event.step_index,
            event_type=_event_type_value(anchor_event),
        ))

    return finish(QuoteLocation(rung=RUNG_UNRESOLVABLE, region=REGION_NONE))


_Finder = Callable[[str, str], Optional[Tuple[int, int]]]
_Mapper = Callable[[str], Tuple[str, List[Tuple[int, int]]]]


def _find_mapped(
    needle: str, haystack: str, canonical: Callable[[str], str], with_map: _Mapper
) -> Optional[Tuple[int, int]]:
    """Span of an already-canonical ``needle`` in ``haystack``, in raw offsets.

    The regex-based ``canonical`` is the fast pre-check; the offset map is
    built only for a haystack that contains the needle.
    """
    if not needle or needle not in canonical(haystack):
        return None
    mapped, spans = with_map(haystack)
    start = mapped.find(needle)
    if start < 0:  # pragma: no cover - the map is equivalent by construction
        return None
    end = start + len(needle)
    return spans[start][0], spans[end - 1][1]


def _find_normalized(needle: str, haystack: str) -> Optional[Tuple[int, int]]:
    """Span of ``needle`` in ``haystack`` after ``_norm``, mapped back to raw offsets."""
    return _find_mapped(needle, haystack, _norm, _norm_with_map)


def _find_canon(needle: str, haystack: str) -> Optional[Tuple[int, int]]:
    """Span of ``needle`` in ``haystack`` after ``_canon``, mapped back to raw offsets."""
    return _find_mapped(needle, haystack, _canon, _canon_with_map)


def _find_exact(needle: str, haystack: str) -> Optional[Tuple[int, int]]:
    start = haystack.find(needle)
    if start < 0:
        return None
    return start, start + len(needle)


def _verbatim(text: str) -> str:
    return text


#: The rungs :func:`locate_quote` climbs, strictest first: how the quote is
#: prepared, and how a haystack is searched for it.
_LADDER: Tuple[Tuple[str, Callable[[str], str], _Finder], ...] = (
    (RUNG_EXACT, _verbatim, _find_exact),
    (RUNG_NORMALIZED, _norm_quote, _find_normalized),
    (RUNG_NORMALIZED, _canon_quote, _find_canon),
)


def _find_in_event(
    event: AgentEvent,
    index: int,
    needle: str,
    shown: Optional[Mapping[str, str]],
    rung: str,
    finder: _Finder,
) -> Optional[QuoteLocation]:
    for field in _EVENT_FIELDS:
        text = _field_text(event, field)
        if not text:
            continue
        span = finder(needle, text)
        if span is not None:
            return QuoteLocation(
                rung=rung, region=REGION_EVENT, event_id=event.event_id,
                event_index=index, step_index=event.step_index,
                event_type=_event_type_value(event), field=field, span=span,
                text=text[span[0]:span[1]],
            )
    rendered = (shown or {}).get(event.event_id)
    if rendered:
        span = finder(needle, rendered)
        if span is not None:
            return QuoteLocation(
                rung=rung, region=REGION_SHOWN, event_id=event.event_id,
                event_index=index, step_index=event.step_index,
                event_type=_event_type_value(event), span=span,
                text=rendered[span[0]:span[1]],
            )
    return None


@dataclass(frozen=True)
class QuoteGrounding:
    """One quote of a finding, located and placed relative to the blamed event.

    ``position`` is ``before`` / ``at`` / ``after`` the blamed event, or
    ``None`` when either side is unknown. ``at_or_before_blame`` is the
    question a consumer of the finding actually asks: was this text in front
    of the agent when it acted? Evidence from a later event is hindsight, and
    a diagnosis resting on it is reasoning from the outcome rather than from
    what was visible.
    """

    role: str
    quote: str
    location: QuoteLocation
    position: Optional[str] = None

    @property
    def at_or_before_blame(self) -> Optional[bool]:
        if self.position is None:
            return None
        return self.position in ('before', 'at')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'role': self.role,
            'quote': self.quote,
            'location': self.location.to_dict(),
            'position': self.position,
            'at_or_before_blame': self.at_or_before_blame,
        }


@dataclass(frozen=True)
class FindingGrounding:
    """Per-finding resolution of its quotes against the trajectory."""

    finding_id: str
    blamed_event_id: Optional[str]
    blamed_event_index: Optional[int]
    blamed_step_index: Optional[int]
    quotes: Tuple[QuoteGrounding, ...]

    @property
    def grounded(self) -> Optional[bool]:
        """``None`` when the finding carries no quotes, matching ``quote_verified``.

        Otherwise every quote must be located under a grounding rung, none may
        come from after the blamed event, and the wrong-content quote -- the
        claim about the blamed step itself -- must be in that step.
        """
        if not self.quotes:
            return None
        for q in self.quotes:
            if not q.location.grounded or not q.at_or_before_blame:
                return False
            if q.role == 'wrong_content' and q.position != 'at':
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'finding_id': self.finding_id,
            'blamed_event_id': self.blamed_event_id,
            'blamed_event_index': self.blamed_event_index,
            'blamed_step_index': self.blamed_step_index,
            'quotes': [q.to_dict() for q in self.quotes],
            'grounded': self.grounded,
        }


def _position(
    location: QuoteLocation, blamed: Optional[AgentEvent], events: Sequence[AgentEvent]
) -> Optional[str]:
    """Where a located quote sits relative to the blamed event.

    Compared by ``step_index`` when both carry one, since several events
    share a step (the agent's call and the tool's result) and "at the blamed
    step" has to cover all of them; by position in the trajectory otherwise.
    """
    if blamed is None or location.event_index is None:
        return None
    located_step = location.step_index
    if located_step is not None and blamed.step_index is not None:
        a, b = located_step, blamed.step_index
    else:
        a, b = location.event_index, events.index(blamed)
    if a < b:
        return 'before'
    if a == b:
        return 'at'
    return 'after'


def grounds_trajectory(
    findings: Iterable[FailureFinding],
    trajectory: AgentTrajectory,
    shown: Optional[Mapping[str, str]] = None,
) -> List[FindingGrounding]:
    """Locate every quote of every finding and place it relative to the blame.

    The wrong-content quote is located with the blamed event as its anchor,
    since that is where the finding says it came from; the reference quote
    carries no anchor unless it embeds one. Findings with neither quote get an
    empty ``quotes`` tuple and ``grounded=None`` -- the rule packs have no
    claim to quote, and that is not a failure to ground.
    """
    events = list(trajectory.events)
    out: List[FindingGrounding] = []
    for finding in findings:
        blamed = _blamed_event(finding, events)
        quotes: List[QuoteGrounding] = []
        for role, quote, anchor in (
            ('wrong_content', finding.wrong_content_quote,
             blamed.event_id if blamed is not None else None),
            ('reference', finding.reference_quote, None),
        ):
            if not quote:
                continue
            location = locate_quote(trajectory, quote, anchor=anchor, shown=shown)
            quotes.append(QuoteGrounding(
                role=role, quote=quote, location=location,
                position=_position(location, blamed, events),
            ))
        out.append(FindingGrounding(
            finding_id=finding.finding_id,
            blamed_event_id=blamed.event_id if blamed is not None else None,
            blamed_event_index=events.index(blamed) if blamed is not None else None,
            blamed_step_index=blamed.step_index if blamed is not None else None,
            quotes=tuple(quotes),
        ))
    return out


def annotate_evidence_regions(
    findings: Iterable[FailureFinding],
    trajectory: AgentTrajectory,
    shown: Optional[Mapping[str, str]] = None,
) -> List[FailureFinding]:
    """Record each finding's grounding in ``metadata['evidence_regions']``.

    Sits next to ``quote_verified``: the boolean says whether the quotes
    resolve, this says where, so a consumer storing the finding can later
    check that its evidence precedes the step it blames without re-running
    the detector.
    """
    annotated = list(findings)
    for finding, grounding in zip(annotated, grounds_trajectory(annotated, trajectory, shown)):
        finding.metadata['evidence_regions'] = grounding.to_dict()
    return annotated
