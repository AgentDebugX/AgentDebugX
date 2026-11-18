"""Dependency-guided failure attribution.

The attributors in :mod:`agentdebug.diagnose.attribute.attribution` answer "which step was
decisive". This one answers the question that usually precedes it: *which step produced the
value the failing step consumed*.

A malformed argument is rarely invented where it is used. A planner records
``refund_policy=waived`` at step 1, the browser passes that value to a booking tool at step
3, and the tool rejects it at step 4. Step-order attributors reach step 1 only by accident
(it is earliest); a model attributor gets there by reading the whole trace. This one gets
there deterministically at zero token cost.

Producer and consumer are kept apart on purpose. A step **produces** the values in its
``output`` (and in ``error``, when an assistant or tool step reports a malformed value);
a step **consumes** the values in its ``input``. An edge exists only when a token one step
produced is a token a later step consumed, so a step that merely mentions a value -- a user
prompt repeating it, a consumer echoing an argument it was handed -- can never become the
producer of that value. User-origin events are excluded from the producer role outright:
they are input to the system, not a link in its dataflow.

Attribution is a graph walk, not a text scan. From the finding's step the walk follows
incoming producer edges on the quoted value and continues through every earlier relay until
it reaches the earliest producer in the chain. That is what makes a two-hop handoff
``step 1 -> step 2 -> step 3`` resolve to step 1 instead of to the more recent relay at
step 2. ``candidate_steps()`` runs the same walk, so ``BinarySearchAttributor``'s pinned
window inherits exactly the ranking that produced the blame.

Nodes are events, not step numbers. Several events can share a ``step_index`` (a plan and
the tool call it spawns, a retry and its result), so the graph addresses each event by its
position in ``trajectory.events``. That keeps ``Blame.span_id`` pointing at the exact event
that produced the value instead of the first event that happens to share its step.

The finding's own fields are read in strict priority order and never pooled. A non-empty
``wrong_content_quote`` is the only field traced: if no earlier step produced the value it
quotes, the finding's own step is blamed, because a ``reference_quote`` names the *correct*
constraint the step failed to satisfy and must never be reported as the source of a wrong
value. ``reference_quote``, and then ``evidence``, are read only when the quote is empty.

Honest limits, stated because the output is a `Blame`:

* The graph is over *produced and consumed values*, not a real dataflow analysis. Two steps
  that pass the same identifier look connected whether or not one handed it to the other,
  so a producer found this way is a candidate with provenance, not a proof of causation.
  ``raw['paths']`` names the value, the field it came from, and every step on the walk.
* When no producer edge reaches the finding's step, nothing is invented: the finding's own
  step is blamed and ``sources`` records ``no_upstream_producer``.
* ``chain_membership`` follows the `Blame` contract in ``attribution.py``: does the traced
  value reach the trajectory's *terminal* step, not merely a later one. Forward traversal
  is scoped to the tracked value, so a step that passes some *other* value onward does not
  drag the chain to the end. It stays ``None`` when there were no values to track, and
  ``False`` when the value dies mid-chain -- an isolated intermediate error the run did not
  ultimately fail on.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

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

#: Speaker names that mark an event as user-origin. Their values are inputs to the run,
#: never a link in the agent's dataflow, so they are not producers.
_USER_AGENT_NAMES = frozenset({'user', 'human'})

#: Event types that mark an event as user-origin, including the ``user_input`` label some
#: importers emit alongside the schema's own ``human.feedback``.
_USER_EVENT_TYPES = frozenset({'user_input', 'human.feedback'})

#: Tokens shorter than this are too generic to attribute anything to.
_MIN_VALUE_LEN = 4

#: Width of the verbatim excerpt quoted as evidence.
_EXCERPT_WIDTH = 90

#: Which finding field to read, most authoritative first. A non-empty ``wrong_content_quote``
#: is read alone -- no falling back to a field that describes the *correct* constraint -- and
#: the weaker fields are consulted only when it is empty. They are never pooled.
_FIELD_PRECEDENCE = ('wrong_content_quote', 'reference_quote', 'evidence')


class _Step(NamedTuple):
    """One event, split into what it emitted and what it took in.

    ``pos`` is the event's index in ``trajectory.events`` and is the node identity: two
    events can share a ``step_index``, so the step number alone cannot name a producer.
    """

    pos: int
    step: int
    event: AgentEvent
    produced: Set[str]
    consumed: Set[str]


class _Edge(NamedTuple):
    """``producer_node -> consumer_node`` carrying one shared value.

    Nodes are event positions; the step numbers are carried along only for reporting, so
    external output keeps speaking in integer ``step_index`` values.
    """

    producer_node: int
    consumer_node: int
    producer_step: int
    consumer_step: int
    token: str
    producer_event_id: str
    consumer_event_id: str


class _Match(NamedTuple):
    """The producer the graph walk settled on, and how it got there."""

    field: str
    producer_node: int
    producer_step: int
    producer_event: AgentEvent
    tokens: List[str]
    chain_nodes: List[int]
    chain_steps: List[int]
    alternates: List[Tuple[int, str]]


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
        #: otherwise writes hundreds of pairs into every report. Traversal always uses
        #: the full graph; only the report is capped.
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
        rows, edges, incoming, outgoing = _graph(events)
        terminal_step = max(row.step for row in rows) if rows else None
        edge_report, truncated = _edge_report(edges, self.max_edges)

        merged: Dict[Tuple[Optional[int], Optional[str]], Blame] = {}
        paths: List[Dict[str, Any]] = []
        for finding in findings:
            blame, path = self._blame_for(finding, rows, incoming, outgoing, terminal_step)
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
                    'edges': edge_report,
                    'edges_truncated': truncated,
                    'terminal_step': terminal_step,
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

        Walks the same producer graph as :meth:`attribute`, so a pinned window sees the
        step this attributor would blame. Ordered by how many values the producer shares
        with the finding, then by depth (earliest producer first), then the findings' own
        steps. Sized for ``BinarySearchAttributor(always_include_steps=...)``: the bisect
        probes elide the middle of a long prefix, so a producer the graph already located
        should not depend on the window happening to include it.
        """

        findings = findings or []
        rows, _edges, incoming, _outgoing = _graph(
            _candidate_step_events(trajectory, list(trajectory.events))
        )
        scored: List[Tuple[int, int]] = []
        own_steps: List[int] = []
        for finding in findings:
            if finding.step_index is None:
                continue
            match = _producer_for(finding, rows, incoming)
            if match is not None:
                scored.append((match.producer_step, len(match.tokens)))
            own_steps.append(finding.step_index)
        ranked = _dedupe(
            [step for step, _count in sorted(scored, key=lambda hit: (-hit[1], hit[0]))]
        )
        return ranked + [step for step in _dedupe(own_steps) if step not in ranked]

    # -- internals ---------------------------------------------------------
    def _blame_for(
        self,
        finding: FailureFinding,
        rows: Sequence[_Step],
        incoming: Dict[Tuple[int, str], List[int]],
        outgoing: Dict[int, List[_Edge]],
        terminal_step: Optional[int],
    ) -> Tuple[Blame, Dict[str, Any]]:
        step = finding.step_index
        match = _producer_for(finding, rows, incoming)
        node = _node_for(rows, step, finding.event_id)

        if match is not None:
            producer_event = match.producer_event
            blame = Blame(
                span_id=producer_event.event_id,
                step_index=match.producer_step,
                agent_name=producer_event.agent_name,
                confidence=round(min(0.95, 0.55 + 0.1 * len(match.tokens)), 2),
                rationale=(
                    f'Step {match.producer_step} produced {match.tokens[0]!r}, which the '
                    f'finding at step {step} consumes. Traced along producer -> consumer '
                    f'edges ({_chain_text(match.chain_steps)}), not step order.'
                ),
                evidence=[_excerpt(_produced_text(producer_event), match.tokens[0])],
                sources=[self.id, 'dependency_producer'],
                chain_membership=_chain_membership(
                    match.producer_node, match.tokens, rows, outgoing, terminal_step
                ),
            )
            field = match.field
        else:
            blame = Blame(
                span_id=finding.event_id,
                step_index=step,
                agent_name=finding.agent_name,
                confidence=confidence_or_default(finding.confidence),
                rationale=(
                    f'{finding.failure_mode.name} at step {step}: no earlier step produced '
                    'a tracked value this finding consumes, so the finding itself is blamed.'
                ),
                evidence=[
                    quote
                    for quote in (finding.wrong_content_quote, finding.reference_quote)
                    if quote
                ],
                sources=[self.id, 'no_upstream_producer'],
                chain_membership=_chain_membership(
                    node, _finding_tokens(finding), rows, outgoing, terminal_step
                ),
            )
            field = None

        return blame, {
            'finding_event_id': finding.event_id,
            'blamed_step': step,
            'producer_event_id': match.producer_event.event_id if match else None,
            'producer_step': match.producer_step if match else None,
            'field': field,
            'values': match.tokens if match else _finding_tokens(finding),
            'chain': list(match.chain_steps) if match else [],
            'alternate_producers': [
                {'step': alt_step, 'value': token} for alt_step, token in (match.alternates if match else [])
            ],
        }


# ---------------------------------------------------------------------------
# Producer / consumer extraction
# ---------------------------------------------------------------------------


def _event_type_value(event: AgentEvent) -> str:
    return str(getattr(event.event_type, 'value', event.event_type))


def _is_user_origin(event: AgentEvent) -> bool:
    """Whether an event is user input rather than agent or tool output."""

    if _event_type_value(event) in _USER_EVENT_TYPES:
        return True
    return (event.agent_name or '').strip().lower() in _USER_AGENT_NAMES


def _stringify(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


def _as_text(value: Any) -> str:
    if value is None:
        return ''
    return value if isinstance(value, str) else _stringify(value)


def _produced_text(event: AgentEvent) -> str:
    """What this step emitted: its output, plus the error it reported."""

    return ' '.join(text for text in (_as_text(event.output), _as_text(event.error)) if text)


def _consumed_text(event: AgentEvent) -> str:
    """What this step took in: its input only."""

    return _as_text(event.input)


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


def _step_rows(events: Sequence[AgentEvent]) -> List[_Step]:
    """``(pos, step, event, produced, consumed)`` per event.

    A user-origin event contributes to neither side: it is not a producer, and a value it
    repeats is not evidence that the run's dataflow passed through it.
    """

    rows: List[_Step] = []
    for position, event in enumerate(events):
        step = event.step_index if event.step_index is not None else position
        if _is_user_origin(event):
            rows.append(_Step(position, step, event, set(), set()))
            continue
        rows.append(
            _Step(
                position,
                step,
                event,
                _value_tokens(_produced_text(event)),
                _value_tokens(_consumed_text(event)),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Graph construction and traversal
# ---------------------------------------------------------------------------


def _build_edges(rows: Sequence[_Step]) -> List[_Edge]:
    """Link each consumed value to the most recent earlier step that produced it."""

    # token -> (node, step, event_id) of the most recent event that emitted it.
    last_producer: Dict[str, Tuple[int, int, str]] = {}
    edges: List[_Edge] = []
    seen: Set[Tuple[int, int, str]] = set()
    for row in rows:
        for token in sorted(row.consumed):
            producer = last_producer.get(token)
            # Strictly earlier by event position, and one relay per value: an event cannot
            # consume from itself, and re-producing a value it already consumed adds no
            # edge. Position rather than step order, so events sharing a ``step_index``
            # stay distinct nodes.
            if producer is None or producer[0] >= row.pos:
                continue
            key = (producer[0], row.pos, token)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                _Edge(
                    producer_node=producer[0],
                    consumer_node=row.pos,
                    producer_step=producer[1],
                    consumer_step=row.step,
                    token=token,
                    producer_event_id=producer[2],
                    consumer_event_id=row.event.event_id,
                )
            )
        for token in row.produced:
            last_producer[token] = (row.pos, row.step, row.event.event_id)
    return edges


def _graph(
    events: Sequence[AgentEvent],
) -> Tuple[List[_Step], List[_Edge], Dict[Tuple[int, str], List[int]], Dict[int, List[_Edge]]]:
    rows = _step_rows(events)
    edges = _build_edges(rows)
    incoming: Dict[Tuple[int, str], List[int]] = {}
    outgoing: Dict[int, List[_Edge]] = {}
    for edge in edges:
        incoming.setdefault((edge.consumer_node, edge.token), []).append(edge.producer_node)
        outgoing.setdefault(edge.producer_node, []).append(edge)
    for producers in incoming.values():
        producers.sort()
    return rows, edges, incoming, outgoing


def _walk_back(
    node: int,
    token: str,
    incoming: Dict[Tuple[int, str], List[int]],
) -> List[int]:
    """Follow producer edges on one value back to its earliest origin.

    Returns the node path ``[earliest, ..., node]``. A single-element result means nothing
    upstream produced the token for this event.
    """

    chain = [node]
    seen = {node}
    current = node
    while True:
        producers = [p for p in incoming.get((current, token), []) if p not in seen]
        if not producers:
            break
        current = min(producers)
        seen.add(current)
        chain.append(current)
    return chain[::-1]


def _forward_reachable(
    node: int,
    tokens: Sequence[str],
    outgoing: Dict[int, List[_Edge]],
) -> Set[int]:
    """Nodes reachable from ``node`` by following edges that carry a tracked value.

    Only the tracked tokens may be followed. A step that consumes token ``X`` and emits an
    unrelated ``Y`` is a bridge between two different dataflows, not a relay of ``X``, so
    traversal stops there rather than riding ``Y`` to the terminal step.
    """

    tracked = set(tokens)
    reachable: Set[int] = set()
    frontier = [node]
    while frontier:
        current = frontier.pop()
        for edge in outgoing.get(current, []):
            if edge.token not in tracked or edge.consumer_node in reachable:
                continue
            reachable.add(edge.consumer_node)
            frontier.append(edge.consumer_node)
    return reachable


def _event_at(rows: Sequence[_Step], node: int) -> Optional[AgentEvent]:
    """The event at graph node ``node`` -- its position in ``trajectory.events``."""

    row = _row_at(rows, node)
    return row.event if row is not None else None


def _row_at(rows: Sequence[_Step], node: int) -> Optional[_Step]:
    for row in rows:
        if row.pos == node:
            return row
    return None


def _node_for(
    rows: Sequence[_Step], step: Optional[int], event_id: Optional[str] = None
) -> Optional[int]:
    """The node for a finding: its own event when known, else the first event at ``step``."""

    if event_id:
        for row in rows:
            if row.event.event_id == event_id:
                return row.pos
    if step is None:
        return None
    for row in rows:
        if row.step == step:
            return row.pos
    return None


# ---------------------------------------------------------------------------
# Finding fields and their strict precedence
# ---------------------------------------------------------------------------


def _tokens_in(texts: Sequence[Any]) -> List[str]:
    tokens: List[str] = []
    for text in texts:
        if not text:
            continue
        for token in sorted(_value_tokens(str(text))):
            if token not in tokens:
                tokens.append(token)
    return tokens


def _target_groups(finding: FailureFinding) -> List[Tuple[str, List[str]]]:
    """The finding fields worth tracing, already narrowed by strict precedence.

    A non-empty ``wrong_content_quote`` is read alone. Falling back from it to
    ``reference_quote`` would blame the step that supplied a *correct* constraint for a
    value the finding says is wrong, so when its tokens reach no producer the finding's own
    step is blamed instead. The weaker fields are read only when that quote is empty.
    """

    wrong_content_quote = finding.wrong_content_quote
    if wrong_content_quote and str(wrong_content_quote).strip():
        return [(_FIELD_PRECEDENCE[0], _tokens_in([wrong_content_quote]))]

    groups: List[Tuple[str, List[str]]] = []
    for field, texts in (
        ('reference_quote', [finding.reference_quote]),
        ('evidence', list(finding.evidence or [])),
    ):
        tokens = _tokens_in(texts)
        if tokens:
            groups.append((field, tokens))
    return groups


def _finding_tokens(finding: FailureFinding) -> List[str]:
    pooled: List[str] = []
    for _field, tokens in _target_groups(finding):
        for token in tokens:
            if token not in pooled:
                pooled.append(token)
    return pooled


def _producer_for(
    finding: FailureFinding,
    rows: Sequence[_Step],
    incoming: Dict[Tuple[int, str], List[int]],
) -> Optional[_Match]:
    """Find the earliest upstream producer, trying each field in precedence order."""

    step = finding.step_index
    if step is None:
        return None
    node = _node_for(rows, step, finding.event_id)
    if node is None:
        return None
    for field, tokens in _target_groups(finding):
        found: List[Tuple[int, str, List[int]]] = []
        for token in tokens:
            chain_nodes = _walk_back(node, token, incoming)
            if len(chain_nodes) >= 2:
                found.append((chain_nodes[0], token, chain_nodes))
        if not found:
            continue
        producer_node = min(entry[0] for entry in found)
        chain_nodes = next(entry[2] for entry in found if entry[0] == producer_node)
        row = _row_at(rows, producer_node)
        if row is None:  # pragma: no cover - defensive
            continue
        return _Match(
            field=field,
            producer_node=producer_node,
            producer_step=row.step,
            producer_event=row.event,
            tokens=sorted(entry[1] for entry in found if entry[0] == producer_node),
            chain_nodes=chain_nodes,
            chain_steps=[
                at.step for at in (_row_at(rows, pos) for pos in chain_nodes) if at
            ],
            alternates=[
                (_row_at(rows, entry[0]).step, entry[1])
                for entry in found
                if entry[0] != producer_node and _row_at(rows, entry[0]) is not None
            ],
        )
    return None


# ---------------------------------------------------------------------------
# Chain membership
# ---------------------------------------------------------------------------


def _chain_membership(
    node: Optional[int],
    tokens: Sequence[str],
    rows: Sequence[_Step],
    outgoing: Dict[int, List[_Edge]],
    terminal_step: Optional[int],
) -> Optional[bool]:
    """Does this event's value reach the trajectory's terminal step?

    Same contract as the rest of the library: ``None`` means unassessed (no tracked
    values), ``False`` means the run did not ultimately fail on this -- the value dies
    before the ending, so the step is a distraction however wrong it was. Propagation is
    followed only along the tracked value, so an unrelated value continuing to the end does
    not make this value in-chain.
    """

    if node is None or not tokens or terminal_step is None:
        return None
    tracked = set(tokens)
    for row in rows:
        if row.step == terminal_step and tracked & (row.produced | row.consumed):
            return True
    reachable = _forward_reachable(node, tokens, outgoing)
    return any(
        row.pos in reachable for row in rows if row.step == terminal_step
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _edge_report(edges: Sequence[_Edge], max_edges: int) -> Tuple[List[Dict[str, Any]], bool]:
    truncated = len(edges) > max_edges
    kept = edges[:max_edges]
    return (
        [
            {
                'from_step': edge.producer_step,
                'from_event_id': edge.producer_event_id,
                'to_step': edge.consumer_step,
                'to_event_id': edge.consumer_event_id,
                'value': edge.token,
            }
            for edge in kept
        ],
        truncated,
    )


def _chain_text(chain: Sequence[int]) -> str:
    return ' -> '.join(str(step) for step in chain)


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
