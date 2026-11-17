"""Dependency-guided failure attribution.

The attributors in :mod:`agentdebug.diagnose.attribute.attribution` answer "which step was
decisive". This one answers the question that usually precedes it: *which step produced the
value the failing step consumed*.

A malformed argument is rarely invented where it is used. A planner records
``refund_policy=waived`` at step 1, the browser passes that value to a booking tool at step
3, and the tool rejects it at step 4. Step-order attributors reach step 1 only by accident
(it is earliest); a model attributor gets there by reading the whole trace. This one gets
there deterministically at zero token cost: it extracts the distinctive values each step
emits, links a step to the earlier step whose value it re-uses, and walks that graph
backwards from every detector finding to the producer of the value it quotes.

Honest limits, stated because the output is a `Blame`:

* The graph is over *shared values*, not a real dataflow analysis. Two steps that happen to
  mention the same identifier look connected, so a producer found this way is a candidate
  with provenance, not a proof of causation. ``raw['paths']`` names the value and both
  steps, so a reader can check the trace instead of trusting the ranking.
* When no earlier step emitted the value, nothing is invented: the finding's own step is
  blamed and ``sources`` records ``no_upstream_producer``.
* ``chain_membership`` is computed only from tracked values and stays ``None`` when there
  were none to track; it is never ``True`` by default.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from agentdebug.diagnose.attribute.attribution import (
    AttributionResult,
    Blame,
    _candidate_step_events,
    _corrected_action_report,
    _score_against_optional_gold,
)
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    FailureFinding,
    confidence_or_default,
)

#: A value-shaped token: identifiers, dotted paths, slugs, versions, quoted spans.
_TOKEN = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.\-/@:]*')

#: Prose that would otherwise look like a shared value and create a fake edge. Kept
#: deliberately small: the shape test below already rejects most English words.
_PROSE = frozenset({
    'about', 'after', 'again', 'agent', 'before', 'being', 'could', 'error', 'failed',
    'failure', 'first', 'found', 'given', 'have', 'here', 'into', 'just', 'like', 'made',
    'make', 'missing', 'must', 'need', 'needs', 'only', 'other', 'over', 'required',
    'same', 'shall', 'should', 'some', 'step', 'such', 'task', 'than', 'that', 'their',
    'them', 'then', 'there', 'these', 'they', 'this', 'those', 'time', 'tool', 'under',
    'used', 'very', 'what', 'when', 'where', 'which', 'will', 'with', 'would', 'your',
})

#: Tokens shorter than this are too generic to attribute anything to.
_MIN_VALUE_LEN = 4

#: Width of the verbatim excerpt quoted as evidence.
_EXCERPT_WIDTH = 90

_Row = Tuple[int, AgentEvent, Set[str]]


class DependencyGraphAttributor:
    """Blame the upstream producer of the value a finding quotes.

    ``requires_findings`` is True for the same reason it is on
    :class:`~agentdebug.diagnose.attribute.attribution.HeuristicAttributor`: this ranks and
    re-anchors detector findings and cannot derive them from a trajectory alone.
    """

    id = 'dependency_graph'

    requires_findings: bool = True

    def __init__(self, *, max_edges: int = 200) -> None:
        #: Cap on ``raw['graph']['edges']``. A long trace with rich tool arguments
        #: otherwise writes hundreds of pairs into every report.
        self.max_edges = max_edges

    # -- public API --------------------------------------------------------
    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        started = time.perf_counter()
        findings = findings or []
        if not findings:
            return AttributionResult(
                method=self.id,
                hypotheses=[],
                raw={
                    'reason': 'no_findings_supplied',
                    'detail': (
                        'DependencyGraphAttributor traces values from earlier steps into '
                        'later ones and cannot derive findings itself. Call it through '
                        'DiagnosePipeline, or pass findings= explicitly.'
                    ),
                    'requires_findings': True,
                },
            )

        events = _candidate_step_events(trajectory, list(trajectory.events))
        rows = _step_rows(events)
        edges, truncated = _build_edges(rows, max_edges=self.max_edges)

        merged: Dict[Tuple[Optional[int], Optional[str]], Blame] = {}
        paths: List[Dict[str, Any]] = []
        for finding in findings:
            blame, path = self._blame_for(finding, rows)
            paths.append(path)
            key = (blame.step_index, blame.agent_name)
            if key in merged:
                _merge_into(merged[key], blame)
            else:
                merged[key] = blame

        hypotheses = sorted(
            merged.values(),
            key=lambda b: (
                b.step_index is None,
                b.step_index if b.step_index is not None else 10**9,
                -b.confidence,
            ),
        )
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            raw={
                'num_findings': len(findings),
                'num_events': len(events),
                'num_producers': sum(1 for p in paths if p['producer_step'] is not None),
                'graph': {
                    'nodes': len(rows),
                    'edges': edges,
                    'edges_truncated': truncated,
                },
                'paths': paths,
                'eval': (
                    _score_against_optional_gold(trajectory, hypotheses[0])
                    if hypotheses
                    else None
                ),
                # Model-free: there is nothing to ask what should have been done
                # instead, so none is guessed. Same report shape as HeuristicAttributor.
                'corrected_action': _corrected_action_report(
                    requested=False,
                    action=None,
                    reason='no_llm',
                    source=self.id,
                ),
            },
        )

    def candidate_steps(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> List[int]:
        """Step indices worth pinning into a window, most informative first.

        Upstream producers first, ordered by how many values they share with a finding,
        then the findings' own steps. Sized for
        ``BinarySearchAttributor(always_include_steps=...)``: the bisect probes elide the
        middle of a long prefix, so a producer the graph already located should not depend
        on the window happening to include it.
        """
        findings = findings or []
        rows = _step_rows(_candidate_step_events(trajectory, list(trajectory.events)))
        scored: List[Tuple[int, int]] = []
        own_steps: List[int] = []
        for finding in findings:
            if finding.step_index is None:
                continue
            hits = _producer_hits(finding.step_index, _finding_values(finding), rows)
            if hits:
                best = sorted(hits, key=lambda hit: (-len(hit[2]), hit[0]))[0]
                scored.append((best[0], len(best[2])))
            own_steps.append(finding.step_index)
        ranked = _dedupe(
            [step for step, _count in sorted(scored, key=lambda hit: (-hit[1], hit[0]))]
        )
        return ranked + [step for step in _dedupe(own_steps) if step not in ranked]

    # -- internals ---------------------------------------------------------
    def _blame_for(
        self,
        finding: FailureFinding,
        rows: Sequence[_Row],
    ) -> Tuple[Blame, Dict[str, Any]]:
        values = _finding_values(finding)
        step = finding.step_index
        hits = _producer_hits(step, values, rows)
        producer: Optional[Tuple[int, AgentEvent, List[str]]] = hits[0] if hits else None

        if producer is not None:
            producer_step, producer_event, matched = producer
            blame = Blame(
                span_id=producer_event.event_id,
                step_index=producer_step,
                agent_name=producer_event.agent_name,
                confidence=round(min(0.95, 0.55 + 0.1 * len(matched)), 2),
                rationale=(
                    f'Step {producer_step} produced {matched[0]!r}, which the finding at '
                    f'step {step} consumes. Traced by value reuse, not step order.'
                ),
                evidence=[_excerpt(_text_of(producer_event), matched[0])],
                sources=[self.id, 'dependency_producer'],
                chain_membership=_chain_membership(producer_step, matched, rows),
            )
        else:
            blame = Blame(
                span_id=finding.event_id,
                step_index=step,
                agent_name=finding.agent_name,
                confidence=confidence_or_default(finding.confidence),
                rationale=(
                    f'{finding.failure_mode.name} at step {step}: no earlier step emitted '
                    'a tracked value this finding quotes, so the finding itself is blamed.'
                ),
                evidence=[
                    quote
                    for quote in (finding.wrong_content_quote, finding.reference_quote)
                    if quote
                ],
                sources=[self.id, 'no_upstream_producer'],
                chain_membership=_chain_membership(step, values, rows),
            )

        return blame, {
            'finding_event_id': finding.event_id,
            'blamed_step': step,
            'producer_event_id': producer[1].event_id if producer else None,
            'producer_step': producer[0] if producer else None,
            'values': matched if producer else values,
            'alternate_producers': [
                {'step': alt_step, 'event_id': alt_event.event_id, 'values': alt}
                for alt_step, alt_event, alt in hits[1:]
            ],
        }


def _text_of(event: AgentEvent) -> str:
    """Every searchable field of one event, as one string."""
    parts: List[str] = []
    for value in (event.input, event.output, event.error):
        if value is None:
            continue
        parts.append(value if isinstance(value, str) else _stringify(value))
    return ' '.join(parts)


def _stringify(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


def _is_value(token: str) -> bool:
    """Whether a token is distinctive enough to be a trackable value."""
    if len(token) < _MIN_VALUE_LEN or token.lower() in _PROSE:
        return False
    return (
        any(mark in token for mark in ('_', '.', '/', '-', '@', ':'))
        or any(char.isdigit() for char in token)
        or len(token) >= 5
    )


def _value_tokens(text: str) -> Set[str]:
    tokens: Set[str] = set()
    for raw in _TOKEN.findall(text):
        # Sentence punctuation is not part of the value: 'failed:' and 'waived.' must
        # compare equal to 'failed' and 'waived', or prose manufactures its own edges.
        token = raw.rstrip('.,:;!?').lower()
        if _is_value(token):
            tokens.add(token)
    return tokens


def _finding_values(finding: FailureFinding) -> List[str]:
    """What the finding says is wrong, as trackable values, most specific first."""
    found: List[str] = []
    for text in (finding.wrong_content_quote, finding.reference_quote, *finding.evidence):
        if not text:
            continue
        for token in sorted(_value_tokens(str(text))):
            if token not in found:
                found.append(token)
    return found


def _step_rows(events: Sequence[AgentEvent]) -> List[_Row]:
    """``(step, event, values)`` per event; a missing step index falls back to position."""
    rows: List[_Row] = []
    for position, event in enumerate(events):
        step = event.step_index if event.step_index is not None else position
        rows.append((step, event, _value_tokens(_text_of(event))))
    return rows


def _producer_hits(
    step: Optional[int],
    values: Sequence[str],
    rows: Sequence[_Row],
) -> List[Tuple[int, AgentEvent, List[str]]]:
    """Earlier steps that already carried any of these values, in step order."""
    if step is None or not values:
        return []
    want = set(values)
    hits: List[Tuple[int, AgentEvent, List[str]]] = []
    for row_step, event, tokens in rows:
        if row_step >= step:
            continue
        matched = sorted(want & tokens)
        if matched:
            hits.append((row_step, event, matched))
    return hits


def _build_edges(
    rows: Sequence[_Row],
    *,
    max_edges: int,
) -> Tuple[List[Dict[str, str]], bool]:
    """Link each value use to the most recent earlier step that emitted it."""
    produced: Dict[str, str] = {}
    edges: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()
    truncated = False
    for _step, event, tokens in rows:
        for token in sorted(tokens):
            producer = produced.get(token)
            if producer is not None and producer != event.event_id:
                key = (producer, event.event_id, token)
                if key not in seen:
                    if len(edges) >= max_edges:
                        truncated = True
                    else:
                        seen.add(key)
                        edges.append(
                            {
                                'from_event_id': producer,
                                'to_event_id': event.event_id,
                                'value': token,
                            }
                        )
            produced[token] = event.event_id
    return edges, truncated


def _chain_membership(
    step: Optional[int],
    values: Sequence[str],
    rows: Sequence[_Row],
) -> Optional[bool]:
    """Did any of these values reach a later step? ``None`` when untrackable.

    ``False`` only means the tracked value never appears downstream again -- the one thing
    this can check mechanically. An empty value list is unassessed, not False.
    """
    if step is None or not values:
        return None
    tracked = set(values)
    return any(row_step > step and tracked & tokens for row_step, _event, tokens in rows)


def _excerpt(text: str, token: str) -> str:
    """A short verbatim span around ``token``, so the evidence can be checked."""
    index = text.lower().find(token)
    if index < 0:
        return text[:_EXCERPT_WIDTH].strip()
    start = max(0, index - _EXCERPT_WIDTH // 2)
    return text[start : start + _EXCERPT_WIDTH].strip()


def _merge_into(existing: Blame, other: Blame) -> None:
    """Fold a second finding blamed at the same step into the first."""
    for quote in other.evidence:
        if quote and quote not in existing.evidence:
            existing.evidence.append(quote)
    for source in other.sources:
        if source not in existing.sources:
            existing.sources.append(source)
    if other.confidence > existing.confidence:
        existing.confidence = other.confidence
    if existing.chain_membership is None:
        existing.chain_membership = other.chain_membership


def _dedupe(values: Sequence[int]) -> List[int]:
    kept: List[int] = []
    for value in values:
        if value not in kept:
            kept.append(value)
    return kept


__all__ = ['DependencyGraphAttributor']
