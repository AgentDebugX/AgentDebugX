"""Failure attribution.

v0.1 ships two backends, both behind the same :class:`Attributor` protocol:

* :class:`HeuristicAttributor` — uses the earliest finding (with confidence
  tie-break) from a ``DiagnosticReport`` to derive blame. Zero-cost; always
  available; matches what :class:`agentdebug.analyzers.HeuristicAnalyzer` does
  internally today.
* :class:`AllAtOnceAttributor` — feeds the full trajectory + findings to an
  LLM and asks for a single blame hypothesis. Uses the Who&When "All-at-Once"
  method from arXiv:2505.00212.

Both produce an :class:`AttributionResult` carrying a list of :class:`Blame`
hypotheses with confidence, rationale, and source attribution. Honest UX: we
always return ranked hypotheses, never single-point claims.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, cast


# Forward decl so BinarySearchAttributor.attribute can reference _EllipsisEvent
# from the helper render path; defined later in the module.

from agentdebug.core.llm import LLMClient, extract_json_block
from agentdebug.core.models import (
    AgentEvent,
    AgentTrajectory,
    FailureFinding,
    confidence_or_default,
    new_id,
)

LOG = logging.getLogger('agentdebug.attribution')


@dataclass
class Blame:
    span_id: Optional[str]
    step_index: Optional[int]
    agent_name: Optional[str]
    confidence: float
    rationale: str
    evidence: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)


@dataclass
class AttributionResult:
    method: str
    hypotheses: List[Blame]
    elapsed_ms: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class Attributor(Protocol):
    id: str

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        ...


class HeuristicAttributor:
    """Cheap, model-free fallback that picks the earliest highest-confidence finding."""

    id = 'heuristic'

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        if not findings:
            return AttributionResult(method=self.id, hypotheses=[])
        ranked = sorted(
            findings,
            key=lambda f: (
                f.step_index is None,
                f.step_index if f.step_index is not None else 10**9,
                -confidence_or_default(f.confidence),
            ),
        )
        primary = ranked[0]
        return AttributionResult(
            method=self.id,
            hypotheses=[
                Blame(
                    span_id=primary.event_id,
                    step_index=primary.step_index,
                    agent_name=primary.agent_name,
                    confidence=confidence_or_default(primary.confidence),
                    rationale=(
                        f'Earliest finding with strongest available confidence: '
                        f'{primary.failure_mode.name}'
                    ),
                    evidence=list(primary.evidence),
                    sources=[self.id],
                )
            ],
        )


_ATTR_SYSTEM_PROMPT = """You are an AI assistant tasked with analyzing agent conversation history when solving a real world problem.

Respond ONLY with a JSON object matching this schema (no prose, no markdown):

{
  "span_id": "<event_id from the input or null>",
  "step_index": <int or null>,
  "agent_name": "<agent_name from the input or null>",
  "confidence": <float between 0 and 1>,
  "rationale": "<one or two sentences justifying the choice>",
  "evidence": ["<short quoted evidence>", ...]
}

If the trajectory does not appear to have failed, return all fields as null and
confidence 0.
"""


class AllAtOnceAttributor:
    """LLM-based attributor mirroring Who&When's All-at-Once method."""

    id = 'all_at_once'

    def __init__(
        self,
        llm: LLMClient,
        *,
        fallback: Optional[Attributor] = None,
        max_findings: int = 20,
        max_tokens: int = 4096,
        use_ground_truth_context: bool = False,
        save_full_generation: bool = False,
        extra_context: str = '',
    ) -> None:
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_findings = max_findings
        self.max_tokens = max_tokens
        self.use_ground_truth_context = use_ground_truth_context
        self.save_full_generation = save_full_generation
        # Optional extra context (e.g. a retrieved historical reference case)
        # prepended to the prompt. Empty -> no behavioral change.
        self.extra_context = extra_context

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        prompt_user = self._render_prompt(trajectory, findings[: self.max_findings])
        messages = [
            {'role': 'system', 'content': _ATTR_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt_user},
        ]
        result = None
        for _attempt in range(2):
            try:
                result = self.llm.complete(messages=messages, max_tokens=self.max_tokens)
                break
            except Exception as exc:  # pragma: no cover - defensive
                if _attempt == 0:
                    LOG.warning('LLM attribution failed (retrying in 2s): %s', exc)
                    time.sleep(2)
                else:
                    LOG.warning('LLM attribution failed; falling back: %s', exc)
                    return self.fallback.attribute(trajectory, findings)
        parsed = extract_json_block(result.text)
        if not parsed:
            LOG.info('LLM attributor returned no JSON; falling back')
            return self.fallback.attribute(trajectory, findings)
        blame = Blame(
            span_id=self._coerce_str(parsed.get('span_id')),
            step_index=self._coerce_int(parsed.get('step_index')),
            agent_name=self._coerce_str(parsed.get('agent_name')),
            confidence=self._coerce_float(parsed.get('confidence'), default=0.0),
            rationale=str(parsed.get('rationale') or ''),
            evidence=self._coerce_str_list(parsed.get('evidence')),
            sources=[self.id],
        )
        blame = self._normalize_blame(trajectory, blame)
        eval_metrics = _score_against_optional_gold(trajectory, blame)
        full_generation: Dict[str, Any] = {}
        if self.save_full_generation:
            full_generation = {
                'request': {
                    'user': prompt_user,
                    'max_tokens': self.max_tokens,
                },
                'response_text': result.text,
                'parsed': parsed,
            }
        return AttributionResult(
            method=self.id,
            hypotheses=[blame],
            raw={
                'finding_count': len(findings),
                'ground_truth_context_used': bool(
                    self.use_ground_truth_context and _ground_truth_text(trajectory)
                ),
                'eval': eval_metrics,
                **({'full_generation': full_generation} if full_generation else {}),
            },
        )

    def _render_prompt(
        self,
        trajectory: AgentTrajectory,
        findings: List[FailureFinding],
    ) -> str:
        rendered_events = self._attributable_events(list(trajectory.events))
        if not rendered_events:
            rendered_events = list(trajectory.events)
        events_doc = '\n'.join(
            f'Step {e.step_index if e.step_index is not None else "?"} '
            f'[{e.event_id}] {e.agent_name}: '
            f'{(str(e.output) if e.output is not None else str(e.input or ""))}'
            f'{(" | ERROR: " + str(e.error)) if e.error else ""}'
            for e in rendered_events
        ) or '(empty conversation)'
        gt_block = (
            f'The Answer for the problem is: {_ground_truth_text(trajectory)}\n'
            if self.use_ground_truth_context and _ground_truth_text(trajectory)
            else ''
        )
        ref_block = f'{self.extra_context.strip()}\n\n' if self.extra_context.strip() else ''
        return (
            'You are analyzing an agent conversation that tried to solve '
            'a real-world problem.\n'
            f'The problem is: {trajectory.goal or "(unknown goal)"}\n'
            f'{_step_semantics_note(trajectory)}'
            f'{gt_block}'
            f'{ref_block}'
            'Identify which agent made the decisive mistake, at which step, '
            'and why that mistake directly caused the failed outcome.\n\n'
            f"Here's the conversation:\n\n{events_doc}\n"
        )

    def _render_finding(self, finding: FailureFinding) -> str:
        confidence = confidence_or_default(finding.confidence)
        return (
            f'- mode={finding.failure_mode.mode_id} '
            f'agent={finding.agent_name} step={finding.step_index} '
            f'confidence={confidence:.2f} '
            f'evidence={"; ".join(finding.evidence)[:200]}'
        )

    @staticmethod
    def _coerce_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_str_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    @staticmethod
    def _normalize_blame(
        trajectory: AgentTrajectory,
        blame: Blame,
    ) -> Blame:
        events = list(trajectory.events)
        if not events:
            return blame
        attributable = AllAtOnceAttributor._attributable_events(events)
        by_id = {e.event_id: e for e in events}
        matched: Optional[AgentEvent] = None

        if blame.span_id and blame.span_id in by_id:
            matched = by_id[blame.span_id]
        elif blame.step_index is not None:
            same_step = [e for e in events if e.step_index == blame.step_index]
            if same_step:
                matched = next(
                    (
                        e for e in same_step
                        if blame.agent_name is None or e.agent_name == blame.agent_name
                    ),
                    same_step[0],
                )
            else:
                # Models sometimes return a 0-based ordinal even when traces
                # use 1-based or sparse step_index values. Normalize against
                # attributable events first so lifecycle markers are skipped.
                pool = attributable or events
                if 0 <= blame.step_index < len(pool):
                    matched = pool[blame.step_index]

        if matched is None:
            return blame
        valid_span = blame.span_id if blame.span_id in by_id else matched.event_id
        return Blame(
            span_id=valid_span,
            step_index=matched.step_index if matched.step_index is not None else blame.step_index,
            agent_name=matched.agent_name,
            confidence=blame.confidence,
            rationale=blame.rationale,
            evidence=list(blame.evidence),
            sources=list(blame.sources),
        )

    @staticmethod
    def _attributable_events(events: List[AgentEvent]) -> List[AgentEvent]:
        return [e for e in events if _is_attributable_event(e)]


_BISECT_SYSTEM_PROMPT = """You are an AI assistant running the "Binary-Search" attribution method.

You will be given one conversation segment split into UPPER HALF and LOWER
HALF. Decide which half is more likely to contain the single decisive error
step that caused the failed outcome.

CRITICAL OUTPUT RULES:
1. Output ONLY a JSON object. No prose before/after. No markdown fences.
2. Keep "rationale" to ONE short sentence (<= 200 chars).
3. Do NOT include newlines inside string values.
4. Emit the JSON object COMPLETE.

Output ONLY a JSON object:
{"half": "upper" | "lower", "confidence": <0..1>, "rationale": "<short>"}
"""


_STEP_SYSTEM_PROMPT = """You are an AI assistant tasked with evaluating the correctness of each step in an ongoing agent conversation aimed at solving a real-world problem.

Respond ONLY with a JSON object matching this schema (no prose, no markdown):

{
  "is_failure_step": true | false,
  "confidence": <float in [0,1]>,
  "rationale": "<one sentence>",
  "evidence": ["<short quoted evidence>", ...]
}

Be CONSERVATIVE: only return true when the trajectory evidence on this step
specifically caused the cascading failure.
Do not rely on information outside the provided trajectory/context.
"""


class StepByStepAttributor:
    """LLM-based attributor mirroring Who&When's Step-by-Step method.

    Walks the trajectory in order, asking the LLM about each step. Returns
    every step that answered ``is_failure_step=true`` as a Blame hypothesis,
    sorted by step index. Costs O(N) LLM calls; pair with ``max_steps`` to
    bound the budget for long trajectories.
    """

    id = 'step_by_step'

    def __init__(
        self,
        llm: LLMClient,
        *,
        fallback: Optional[Attributor] = None,
        max_steps: Optional[int] = 30,
        context_window: int = 3,
        max_tokens: int = 4096,
        use_ground_truth_context: bool = False,
        save_full_generation: bool = False,
    ) -> None:
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_steps = max_steps
        self.context_window = context_window
        self.max_tokens = max_tokens
        self.use_ground_truth_context = use_ground_truth_context
        self.save_full_generation = save_full_generation

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        events = list(trajectory.events)
        if not events:
            return self.fallback.attribute(trajectory, findings)
        step_events = _candidate_step_events(trajectory, events)
        if not step_events:
            return self.fallback.attribute(trajectory, findings)
        budget = min(self.max_steps, len(step_events)) if self.max_steps is not None else len(step_events)
        scanned = step_events[:budget]
        hypotheses: List[Blame] = []
        generations: List[Dict[str, Any]] = []
        for evt in scanned:
            # Prefix-style evaluation: use the full conversation history
            # from the beginning up to, but not including, the current step.
            history = _raw_history_before_event(events, evt)
            if self.context_window and len(history) > self.context_window:
                history = history[-self.context_window:]
            verdict = self._classify_step(
                trajectory, findings, evt, history=history, raw_events=events
            )
            if self.save_full_generation and verdict is not None:
                generations.append({
                    'event_id': evt.event_id,
                    'step_index': evt.step_index,
                    'agent_name': evt.agent_name,
                    'verdict': verdict,
                })
            if verdict is None or not verdict.get('is_failure_step'):
                continue
            hypotheses.append(Blame(
                span_id=evt.event_id,
                step_index=evt.step_index,
                agent_name=evt.agent_name,
                confidence=self._coerce_float(verdict.get('confidence'), 0.5),
                rationale=str(verdict.get('rationale') or ''),
                evidence=self._coerce_str_list(verdict.get('evidence')),
                sources=[self.id],
            ))
        if not hypotheses:
            return AttributionResult(
                method=self.id,
                hypotheses=[],
                raw={
                    'scanned_steps': len(scanned),
                    'ground_truth_context_used': bool(
                        self.use_ground_truth_context and _ground_truth_text(trajectory)
                    ),
                    'eval': None,
                    **({'full_generation': generations} if self.save_full_generation else {}),
                },
            )
        hypotheses.sort(
            key=lambda h: (
                h.step_index is None,
                h.step_index if h.step_index is not None else 10**9,
            )
        )
        eval_metrics = _score_against_optional_gold(trajectory, hypotheses[0])
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            raw={
                'scanned_steps': len(scanned),
                'ground_truth_context_used': bool(
                    self.use_ground_truth_context and _ground_truth_text(trajectory)
                ),
                'eval': eval_metrics,
                **({'full_generation': generations} if self.save_full_generation else {}),
            },
        )

    def _classify_step(
        self,
        trajectory: AgentTrajectory,
        findings: List[FailureFinding],
        event: 'AgentEvent',
        *,
        history: List['AgentEvent'],
        raw_events: Optional[List['AgentEvent']] = None,
    ) -> Optional[Dict[str, Any]]:
        history_doc = _render_history_doc(trajectory, history)
        gt_block = (
            f'The Answer for the problem is: {_ground_truth_text(trajectory)}\n'
            if self.use_ground_truth_context and _ground_truth_text(trajectory)
            else ''
        )
        if _step_semantics(trajectory) == 'env_agent_pairs':
            history_doc = _render_pair_history_doc(raw_events or history, event.step_index)
            candidate_text = _render_pair_step_doc(raw_events or [], event.step_index)
        else:
            candidate_text = (
                str(event.output) if event.output is not None else str(event.input or '')
            )
        prompt = (
            'Evaluate whether the latest step is the decisive failure step in '
            'an ongoing agent conversation.\n'
            f'The problem is: {trajectory.goal or "(unknown goal)"}\n'
            f'{_step_semantics_note(trajectory)}'
            f'{gt_block}'
            f'Here is the conversation history up to the current step:\n{history_doc}\n\n'
            'CANDIDATE STEP:\n'
            f'  event_id={event.event_id}\n'
            f'  step={event.step_index} agent={event.agent_name}\n'
            f'  type={getattr(event.event_type, "value", event.event_type)}\n'
            f'  content={candidate_text}\n'
            f'  error={str(event.error) if event.error else ""}\n'
        )
        result = None
        for _attempt in range(2):
            try:
                result = self.llm.complete(
                    messages=[
                        {'role': 'system', 'content': _STEP_SYSTEM_PROMPT},
                        {'role': 'user', 'content': prompt},
                    ],
                    max_tokens=self.max_tokens,
                )
                break
            except Exception as exc:  # pragma: no cover
                if _attempt == 0:
                    LOG.warning('step_by_step LLM call failed at step %s (retrying in 2s): %s',
                                event.step_index, exc)
                    time.sleep(2)
                else:
                    LOG.warning('step_by_step LLM call failed at step %s: %s',
                                event.step_index, exc)
                    return None
        parsed = extract_json_block(result.text)
        if parsed is None:
            return None
        out = cast(Dict[str, Any], parsed)
        if self.save_full_generation:
            out = dict(out)
            out['_request'] = {
                'user': prompt,
                'max_tokens': self.max_tokens,
            }
            out['_response_text'] = result.text
        return out

    def _render_finding(self, finding: FailureFinding) -> str:
        confidence = confidence_or_default(finding.confidence)
        return (
            f'- mode={finding.failure_mode.mode_id} '
            f'agent={finding.agent_name} step={finding.step_index} '
            f'confidence={confidence:.2f}'
        )

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_str_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]


class BinarySearchAttributor:
    """LLM-based attributor implementing Who&When's Binary-Search method.

    Recursively bisects the trajectory into upper and lower halves, asking the
    LLM which half is more likely to contain the decisive error step. Costs
    O(log N) LLM calls vs StepByStep's O(N).

    The contract:

    * Pre-condition: the trajectory is known to have failed overall.
    * Loop invariant: the decisive step lives inside the current inclusive
      segment ``[start, end]``.
    * Termination: ``start == end``; that single event is the decisive step.

    Returns the event at the decisive index as the primary Blame hypothesis.
    Falls back to the configured ``fallback`` attributor when the trajectory
    is empty or the LLM responses are uninterpretable.
    """

    id = 'binary_search'

    def __init__(
        self,
        llm: LLMClient,
        *,
        fallback: Optional[Attributor] = None,
        max_tokens: int = 4096,
        context_window: int = 6,
        use_ground_truth_context: bool = False,
    ) -> None:
        # max_tokens default doubled in 0.2.4: thinking models (Gemini, o-series)
        # consume most of the budget on reasoning before any JSON is emitted, so
        # 1024 was empirically truncating bisect probes in the v0.2.3 E2E.
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_tokens = max_tokens
        # When formatting a prefix into the LLM prompt we only keep this many
        # events at the head + this many at the tail; the middle is elided.
        # Keeps cost bounded for very long trajectories.
        self.context_window = context_window
        self.use_ground_truth_context = use_ground_truth_context

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        events = _candidate_step_events(trajectory, list(trajectory.events))
        n = len(events)
        if n == 0:
            return self.fallback.attribute(trajectory, findings)
        decisive, probe_count = self._find_error_in_segment_recursive(
            trajectory, events, list(trajectory.events), 0, n - 1
        )
        if decisive is None:
            return self.fallback.attribute(trajectory, findings)
        return AttributionResult(
            method=self.id,
            hypotheses=[Blame(
                span_id=decisive.event_id,
                step_index=decisive.step_index,
                agent_name=decisive.agent_name,
                confidence=0.6 + 0.1 * min(probe_count, 4),
                rationale=(
                    f'Binary search located the decisive step within '
                    f'{probe_count} probes over {n} events.'
                ),
                evidence=[
                    f'event_id={decisive.event_id}',
                    f'step={decisive.step_index}',
                ],
                sources=[self.id],
            )],
            raw={
                'probe_count': probe_count,
                'trajectory_len': n,
                'ground_truth_context_used': bool(
                    self.use_ground_truth_context and _ground_truth_text(trajectory)
                ),
                'search_strategy': 'half_recursive',
            },
        )

    def _find_error_in_segment_recursive(
        self,
        trajectory: AgentTrajectory,
        events: List[AgentEvent],
        raw_events: List[AgentEvent],
        start: int,
        end: int,
    ) -> tuple[Optional['AgentEvent'], int]:
        if start > end:
            return None, 0
        if start == end:
            return events[start], 0
        verdict = self._probe_segment(trajectory, events, raw_events, start, end)
        if verdict is None:
            return None, 1
        split = max(1, (end - start + 1) // 2)
        mid = start + split - 1
        if verdict.get('search_half') == 'upper':
            decisive, child_probes = self._find_error_in_segment_recursive(
                trajectory, events, raw_events, start, mid
            )
        elif verdict.get('search_half') == 'lower':
            decisive, child_probes = self._find_error_in_segment_recursive(
                trajectory, events, raw_events, mid + 1, end
            )
        else:
            return None, 1
        return decisive, 1 + child_probes

    def _probe_segment(
        self,
        trajectory: AgentTrajectory,
        events: List[AgentEvent],
        raw_events: Optional[List[AgentEvent]] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        # Backward compatibility for the old call shape:
        #   _probe_segment(trajectory, events, start, end)
        # In that case Python binds ``start`` to ``raw_events`` and ``end`` to
        # ``start``.
        if isinstance(raw_events, int) and isinstance(start, int) and end is None:
            end = start
            start = raw_events
            raw_events = None
        if start is None or end is None:
            raise TypeError('_probe_segment() missing required start/end')
        segment_events = events[start : end + 1]
        if not segment_events:
            return None
        raw_events = raw_events or events
        split = max(1, len(segment_events) // 2)
        upper_events = segment_events[:split]
        lower_events = segment_events[split:]
        upper_doc = self._render_segment_doc(raw_events, upper_events) or '(empty)'
        lower_doc = self._render_segment_doc(raw_events, lower_events) or '(empty)'
        answer = _ground_truth_text(trajectory)
        gt_block = (
            f"The Answer for the problem is: {answer}\n"
            if self.use_ground_truth_context and answer
            else ''
        )
        mid = start + split - 1
        segment_start = segment_events[0].step_index if segment_events[0].step_index is not None else start
        segment_end = segment_events[-1].step_index if segment_events[-1].step_index is not None else end
        upper_start = segment_events[0].step_index if segment_events[0].step_index is not None else start
        upper_end = segment_events[split - 1].step_index if segment_events[split - 1].step_index is not None else mid
        lower_start = segment_events[split].step_index if split < len(segment_events) and segment_events[split].step_index is not None else mid + 1
        lower_end = segment_events[-1].step_index if segment_events[-1].step_index is not None else end
        semantics = _step_semantics(trajectory)
        segment_label = 'steps' if semantics == 'env_agent_pairs' else 'events'
        user = (
            f'The problem to address is as follows: {trajectory.goal!r}\n'
            f'{_step_semantics_note(trajectory)}'
            f'SEGMENT ({segment_label} {segment_start}..{segment_end} of {len(events)}):\n\n'
            f'{gt_block}'
            f'UPPER HALF ({segment_label} {upper_start}..{upper_end}):\n{upper_doc}\n\n'
            f'LOWER HALF ({segment_label} {lower_start}..{lower_end}):\n{lower_doc}\n'
        )
        result = None
        for _attempt in range(2):
            try:
                result = self.llm.complete(
                    messages=[
                        {'role': 'system', 'content': _BISECT_SYSTEM_PROMPT},
                        {'role': 'user', 'content': user},
                    ],
                    max_tokens=self.max_tokens,
                )
                break
            except Exception as exc:  # pragma: no cover
                if _attempt == 0:
                    LOG.warning(
                        'binary_search probe at segment %s..%s failed (retrying in 2s): %s',
                        start, end, exc,
                    )
                    time.sleep(2)
                else:
                    LOG.warning(
                        'binary_search probe at segment %s..%s failed: %s',
                        start, end, exc,
                    )
                    return None
        parsed = extract_json_block(result.text)
        if parsed is None:
            return None
        parsed = cast(Dict[str, Any], parsed)
        half = str(parsed.get('half') or '').strip().lower()
        if half in {'upper', 'lower'}:
            return {
                'search_half': half,
                'confidence': parsed.get('confidence'),
                'rationale': parsed.get('rationale'),
            }

        # Backward compatibility: earlier probes asked whether the failure had
        # already happened in the current prefix. Interpret "already happened"
        # as meaning the decisive step is in the upper half.
        if 'failure_already_happened' in parsed:
            return {
                'search_half': 'upper' if bool(parsed.get('failure_already_happened')) else 'lower',
                'confidence': parsed.get('confidence'),
                'rationale': parsed.get('rationale'),
            }
        return None

    def _render_segment_doc(
        self,
        raw_events: List[AgentEvent],
        assistant_events: List[AgentEvent],
    ) -> str:
        if not assistant_events:
            return '(empty)'
        raw_index = {e.event_id: i for i, e in enumerate(raw_events)}
        assistant_positions = [
            raw_index[e.event_id]
            for e in assistant_events
            if e.event_id in raw_index
        ]
        assistant_positions.sort()
        return self._render_pair_doc(raw_events, assistant_positions)

    def _render_pair_doc(
        self,
        raw_events: List[AgentEvent],
        assistant_positions: List[int],
    ) -> str:
        if not assistant_positions:
            return '(empty)'
        blocks: List[str] = []
        for pos in assistant_positions:
            assistant_event = raw_events[pos]
            step_index = assistant_event.step_index if assistant_event.step_index is not None else pos
            env_lines: List[str] = []
            scan = pos - 1
            while scan >= 0:
                candidate = raw_events[scan]
                if getattr(candidate.event_type, 'value', candidate.event_type) in {
                    'llm.response',
                    'agent.step',
                }:
                    break
                if candidate.step_index != step_index and env_lines:
                    break
                if candidate.step_index != step_index and not env_lines:
                    break
                env_content = (
                    str(candidate.output)
                    if candidate.output is not None
                    else str(candidate.input or '')
                )
                env_content = (
                    f'{env_content}{(" | ERROR: " + str(candidate.error)) if candidate.error else ""}'
                )
                env_lines.append(f'Environment {step_index}: {env_content}')
                scan -= 1
            env_lines.reverse()
            agent_content = (
                str(assistant_event.output)
                if assistant_event.output is not None
                else str(assistant_event.input or '')
            )
            agent_content = (
                f'{agent_content}{(" | ERROR: " + str(assistant_event.error)) if assistant_event.error else ""}'
            )
            block = env_lines + [f'Agent {step_index}: {agent_content}']
            blocks.append('\n'.join(block))
        return '\n\n'.join(blocks) or '(empty)'

    def _render_prefix(self, prefix: AgentTrajectory) -> str:
        events = prefix.events
        if len(events) <= 2 * self.context_window:
            view = events
        else:
            head = events[: self.context_window]
            tail = events[-self.context_window:]
            elided = len(events) - 2 * self.context_window
            view = head + [_EVENT_ELLIPSIS(elided)] + tail
        return '\n'.join(self._render_event(e) for e in view)

    @staticmethod
    def _render_event(event: Any) -> str:
        if isinstance(event, _EllipsisEvent):
            return f'... ({event.count} events elided) ...'
        return (
            f'event_id={event.event_id} step={event.step_index} '
            f'agent={event.agent_name} '
            f'type={getattr(event.event_type, "value", event.event_type)} '
            f'output={str(event.output)} '
            f'error={str(event.error)}'
        )

@dataclass
class _EllipsisEvent:
    count: int


def _EVENT_ELLIPSIS(count: int) -> _EllipsisEvent:
    return _EllipsisEvent(count=count)


def _ground_truth_text(trajectory: AgentTrajectory) -> str:
    """Best-effort optional GT extractor for offline attribution experiments.

    Supports common metadata keys used in benchmark conversion scripts.
    Returns empty string when GT is absent so online/no-GT flows stay unchanged.
    """
    meta = trajectory.metadata or {}
    for key in ('ground_truth', 'answer', 'gold_answer'):
        value = meta.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ''


def _norm_agent(text: Optional[str]) -> str:
    return (text or '').lower().replace('_', ' ').replace('-', ' ').strip()


def _opt_step_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == '':
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_attributable_event(event: AgentEvent) -> bool:
    return getattr(event.event_type, 'value', event.event_type) not in {
        'run.start',
        'run.end',
    }


def _step_semantics(trajectory: AgentTrajectory) -> str:
    meta = trajectory.metadata or {}
    semantics = str(meta.get('step_semantics') or 'all_events').strip().lower()
    if semantics in {'env_agent_pairs', 'paired_turns'}:
        return 'env_agent_pairs'
    if semantics in {'assistant_only', 'assistant_steps', 'assistant_turns'}:
        return 'env_agent_pairs'
    return 'all_events'


def _step_semantics_note(trajectory: AgentTrajectory) -> str:
    if _step_semantics(trajectory) == 'env_agent_pairs':
        return (
            'Step semantics: env_agent_pairs (1-based environment/agent pairs; '
            'each step is an environment observation followed by the agent '
            'response).\n'
        )
    return (
        'Step semantics: all_events (0-based event steps; every attributable '
        'event counts as a step).\n'
    )


def _render_history_doc(
    trajectory: AgentTrajectory,
    history: List[AgentEvent],
) -> str:
    semantics = _step_semantics(trajectory)
    lines: List[str] = []
    for i, event in enumerate(history):
        content = str(event.output) if event.output is not None else str(event.input or '')
        content = f'{content}{(" | ERROR: " + str(event.error)) if event.error else ""}'
        step_index = event.step_index if event.step_index is not None else i
        if semantics == 'env_agent_pairs':
            if getattr(event.event_type, 'value', event.event_type) in {
                'llm.response',
                'agent.step',
            }:
                lines.append(f'Agent {step_index} - {event.agent_name}: {content}')
            else:
                lines.append(f'Environment {step_index} - {event.agent_name}: {content}')
        else:
            lines.append(f'Step {step_index} - {event.agent_name}: {content}')
    return '\n'.join(lines) or '(empty history)'


def _render_pair_history_doc(
    raw_events: List[AgentEvent],
    current_step: Optional[int],
) -> str:
    if not raw_events or current_step is None:
        return '(empty history)'
    lines: List[str] = []
    steps = sorted({
        event.step_index
        for event in raw_events
        if event.step_index is not None and event.step_index < current_step
    })
    for step in steps:
        lines.append(_render_pair_step_doc(raw_events, step))
    return '\n\n'.join(lines) or '(empty history)'


def _render_pair_step_doc(
    raw_events: List[AgentEvent],
    step_index: Optional[int],
) -> str:
    if step_index is None:
        return '(empty)'
    events = [event for event in raw_events if event.step_index == step_index]
    if not events:
        return '(empty)'
    env_lines: List[str] = []
    agent_lines: List[str] = []
    for event in events:
        content = str(event.output) if event.output is not None else str(event.input or '')
        content = f'{content}{(" | ERROR: " + str(event.error)) if event.error else ""}'
        if getattr(event.event_type, 'value', event.event_type) in {
            'llm.response',
            'agent.step',
        }:
            agent_lines.append(f'Agent {step_index}: {content}')
        else:
            env_lines.append(f'Environment {step_index}: {content}')
    return '\n'.join(env_lines + agent_lines) or '(empty)'


def _candidate_step_events(
    trajectory: AgentTrajectory,
    events: Optional[List[AgentEvent]] = None,
) -> List[AgentEvent]:
    pool = list(events if events is not None else trajectory.events)
    if _step_semantics(trajectory) == 'env_agent_pairs':
        return [
            event for event in pool
            if getattr(event.event_type, 'value', event.event_type)
            in {'llm.response', 'agent.step'}
        ]
    return [event for event in pool if _is_attributable_event(event)]


def _raw_history_before_event(
    events: List[AgentEvent],
    event: AgentEvent,
) -> List[AgentEvent]:
    for idx, candidate in enumerate(events):
        if candidate.event_id == event.event_id:
            return list(events[:idx])
    return []


def _score_against_optional_gold(
    trajectory: AgentTrajectory,
    blame: Blame,
) -> Optional[Dict[str, bool]]:
    """Return Who&When-style metrics if gold labels exist in trajectory.metadata.

    Expected optional metadata keys: mistake_agent, mistake_step.
    """
    meta = trajectory.metadata or {}
    gold_agent = _norm_agent(cast(Optional[str], meta.get('mistake_agent')))
    gold_step = _opt_step_int(meta.get('mistake_step'))
    if not gold_agent and gold_step is None:
        return None
    pred_agent = _norm_agent(blame.agent_name)
    pred_step = _opt_step_int(blame.step_index)
    agent_match = bool(
        gold_agent and pred_agent
        and (gold_agent in pred_agent or pred_agent in gold_agent)
    )
    exact_step = gold_step is not None and pred_step == gold_step
    near_step = (
        gold_step is not None
        and pred_step is not None
        and abs(pred_step - gold_step) <= 1
    )
    return {
        'agent_match': agent_match,
        'exact_step': exact_step,
        'near_step': near_step,
        'both_exact': agent_match and exact_step,
        'both_near': agent_match and near_step,
    }


_COUNTERFACTUAL_SYSTEM_PROMPT = """You are AgentDebugX-Attributor running an
LLM-simulated counterfactual replay.

You will be given the goal, the full trajectory, and ONE CANDIDATE STEP. Your
job is to estimate whether the agent would have succeeded if THAT step had
been done correctly — leaving everything else the same. This isolates the
step's causal contribution to the failure.

CRITICAL OUTPUT RULES (these maximize the chance your reply parses):
1. Output ONLY a JSON object. No prose before/after. No markdown fences.
2. Keep "rationale" to ONE short sentence (<= 200 chars).
3. Do NOT include newlines inside string values.
4. Emit the JSON object COMPLETE.

Schema:
{
  "rescue_probability": <0..1>,
  "confidence": <0..1>,
  "rationale": "<short>",
  "would_block_downstream_failures": true | false
}

Higher rescue_probability = correcting this step would more likely have
rescued the run; this step is therefore more responsible for the failure.
"""


class CounterfactualAttributor:
    """LLM-simulated counterfactual replay.

    For each of K candidate steps (top-K from prior findings, or
    error-bearing events, or the tail of the trajectory) ask the LLM:
    "if this step had been correct, would the rest of the trajectory still
    fail?" Steps with the highest rescue-probability become the top blame
    hypotheses. Costs O(K) LLM calls — comparable to AllAtOnce, with a
    stronger causal claim per probe.

    This is *simulated* counterfactual, not real re-rollout — strictly
    weaker than AgenTracer's actual replay, but framework-independent and
    runnable today against any LLM. When the underlying framework gains a
    real replay surface (LangGraph checkpointer, OpenHands rewind), wire
    that in as an alternative ``replay_fn`` and the algorithm carries over.
    """

    id = 'counterfactual'

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_candidates: int = 5,
        max_tokens: int = 2048,
        fallback: Optional[Attributor] = None,
    ) -> None:
        self.llm = llm
        self.max_candidates = max_candidates
        self.max_tokens = max_tokens
        self.fallback: Attributor = fallback or HeuristicAttributor()

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        candidates = self._pick_candidates(trajectory, findings)
        if not candidates:
            return self.fallback.attribute(trajectory, findings)
        ranked: List[tuple[AgentEvent, Dict[str, Any]]] = []
        for evt in candidates:
            verdict = self._ask_counterfactual(trajectory, evt)
            if verdict is None:
                continue
            ranked.append((evt, verdict))
        if not ranked:
            return self.fallback.attribute(trajectory, findings)
        # Sort by rescue_probability desc, tie-break by confidence.
        ranked.sort(
            key=lambda r: (
                -self._coerce_float(r[1].get('rescue_probability'), 0.0),
                -self._coerce_float(r[1].get('confidence'), 0.0),
            )
        )
        hypotheses: List[Blame] = []
        for evt, verdict in ranked:
            hypotheses.append(Blame(
                span_id=evt.event_id,
                step_index=evt.step_index,
                agent_name=evt.agent_name,
                confidence=self._coerce_float(verdict.get('rescue_probability'), 0.0),
                rationale=(
                    str(verdict.get('rationale') or 'no rationale')
                    + f' [rescue_probability={verdict.get("rescue_probability")}]'
                ),
                evidence=[
                    f'event_id={evt.event_id}',
                    f'step={evt.step_index}',
                ],
                sources=[self.id],
            ))
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            raw={'candidates_probed': len(ranked)},
        )

    def _pick_candidates(
        self,
        trajectory: AgentTrajectory,
        findings: List[FailureFinding],
    ) -> List[AgentEvent]:
        events_by_id = {e.event_id: e for e in trajectory.events}
        candidates: List[AgentEvent] = []
        seen: set[str] = set()
        # 1. Prior findings (the judge already nominated suspects).
        for f in findings:
            evt = events_by_id.get(f.event_id) if f.event_id else None
            if evt is not None and evt.event_id not in seen:
                candidates.append(evt)
                seen.add(evt.event_id)
                if len(candidates) >= self.max_candidates:
                    return candidates
        # 2. Events that recorded an error directly.
        for evt in trajectory.events:
            if evt.error and evt.event_id not in seen:
                candidates.append(evt)
                seen.add(evt.event_id)
                if len(candidates) >= self.max_candidates:
                    return candidates
        # 3. Fallback: tail of the trajectory (failure most often manifests there).
        for evt in reversed(trajectory.events):
            if evt.event_id not in seen:
                candidates.append(evt)
                seen.add(evt.event_id)
                if len(candidates) >= self.max_candidates:
                    return candidates
        return candidates

    def _ask_counterfactual(
        self, trajectory: AgentTrajectory, candidate: AgentEvent,
    ) -> Optional[Dict[str, Any]]:
        events_doc = '\n'.join(
            f'event_id={e.event_id} step={e.step_index} agent={e.agent_name} '
            f'type={getattr(e.event_type, "value", e.event_type)} '
            f'output={str(e.output)[:200]} error={str(e.error)[:200]}'
            for e in trajectory.events
        )
        user = (
            f'GOAL: {trajectory.goal!r}\n'
            f'FRAMEWORK: {trajectory.framework!r}\n\n'
            f'FULL TRAJECTORY:\n{events_doc}\n\n'
            f'CANDIDATE STEP TO COUNTERFACTUALLY CORRECT:\n'
            f'  event_id={candidate.event_id}\n'
            f'  step={candidate.step_index} agent={candidate.agent_name}\n'
            f'  module={candidate.module}\n'
            f'  input={str(candidate.input)[:300]}\n'
            f'  output={str(candidate.output)[:300]}\n'
            f'  error={str(candidate.error)[:300]}\n\n'
            f'Question: if this step had been DONE CORRECTLY, what is the '
            f'probability the run would have succeeded?'
        )
        try:
            result = self.llm.complete(
                messages=[
                    {'role': 'system', 'content': _COUNTERFACTUAL_SYSTEM_PROMPT},
                    {'role': 'user', 'content': user},
                ],
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # pragma: no cover
            LOG.warning('counterfactual probe failed at event=%s: %s',
                        candidate.event_id, exc)
            return None
        parsed = extract_json_block(result.text)
        if parsed is None:
            return None
        return cast(Dict[str, Any], parsed)

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


class SBFLAttributor:
    """Spectrum-based fault localization (Tarantula / Ochiai / DStar).

    Model-free. Costs ZERO LLM calls at inference time. The price: it needs
    a corpus of *other* traces of the same task (a mix of passing and
    failing). For each step in the failing trace under attribution, compute
    suspiciousness from how often that step's signature appears in failing
    vs passing traces of the same task.

    Signature design — the cross-trace identity of a "step":

      (agent_name, event_type, module, normalized_io_hash)

    The normalized I/O hash collapses whitespace + truncates to 120 chars
    so semantically-equivalent step executions match across runs (a
    `search` call with the same query produces the same signature even if
    the wrapping prompt differs trace-to-trace).

    Suspiciousness formulas:

      Tarantula = (ef / nf) / (ef / nf + ep / np)
      Ochiai    = ef / sqrt((ef + ep) * (nf))
      DStar*    = ef^* / (ep + (nf - ef))     [* = exponent, default 2]

    where for the candidate step:
      ef = # failing corpus traces containing this signature
      ep = # passing corpus traces containing this signature
      nf = total failing traces in the corpus
      np = total passing traces in the corpus

    Reference: Jones & Harrold (Tarantula, ICSE 2002); Abreu et al.
    (Ochiai, TR 2007); Wong et al. (DStar, IEEE TR 2014).
    """

    id = 'sbfl'

    def __init__(
        self,
        *,
        passing_corpus: List[AgentTrajectory],
        failing_corpus: List[AgentTrajectory],
        formula: str = 'ochiai',
        dstar_exponent: float = 2.0,
        top_k: int = 5,
        fallback: Optional[Attributor] = None,
    ) -> None:
        if formula not in {'tarantula', 'ochiai', 'dstar'}:
            raise ValueError(
                f"Unknown SBFL formula {formula!r}; "
                "use 'tarantula', 'ochiai', or 'dstar'"
            )
        self.passing_corpus = list(passing_corpus)
        self.failing_corpus = list(failing_corpus)
        self.formula = formula
        self.dstar_exponent = dstar_exponent
        self.top_k = top_k
        self.fallback: Attributor = fallback or HeuristicAttributor()
        # Precompute signature presence per trace so attribution is O(N) over
        # the candidate's events × O(corpus_size).
        self._sigs_in_failing: List[set[str]] = [
            _signatures_for(t) for t in self.failing_corpus
        ]
        self._sigs_in_passing: List[set[str]] = [
            _signatures_for(t) for t in self.passing_corpus
        ]

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        nf = len(self._sigs_in_failing)
        np = len(self._sigs_in_passing)
        if nf == 0 and np == 0:
            return self.fallback.attribute(trajectory, findings)

        scored: List[tuple[AgentEvent, float, int, int]] = []
        for evt in trajectory.events:
            sig = _signature_for(evt)
            if not sig:
                continue
            ef = sum(1 for s in self._sigs_in_failing if sig in s)
            ep = sum(1 for s in self._sigs_in_passing if sig in s)
            score = _suspiciousness(
                ef=ef, ep=ep, nf=max(nf, 1), np=max(np, 1),
                formula=self.formula, dstar_exponent=self.dstar_exponent,
            )
            scored.append((evt, score, ef, ep))

        if not scored:
            return self.fallback.attribute(trajectory, findings)

        # Sort by suspiciousness desc, tie-break by step index asc (earlier wins).
        scored.sort(key=lambda r: (-r[1], r[0].step_index or 10**9))
        hypotheses: List[Blame] = []
        for evt, score, ef, ep in scored[: self.top_k]:
            if score <= 0.0:
                continue  # nothing suspicious about this step
            hypotheses.append(Blame(
                span_id=evt.event_id,
                step_index=evt.step_index,
                agent_name=evt.agent_name,
                confidence=min(1.0, max(0.0, score)),
                rationale=(
                    f'{self.formula} suspiciousness={score:.3f} '
                    f'(ef={ef}/{nf}, ep={ep}/{np})'
                ),
                evidence=[
                    f'signature occurs in {ef}/{nf} failing and {ep}/{np} '
                    f'passing corpus traces',
                ],
                sources=[self.id],
            ))
        if not hypotheses:
            return self.fallback.attribute(trajectory, findings)
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            raw={
                'formula': self.formula,
                'corpus_size': {'failing': nf, 'passing': np},
                'scored_events': len(scored),
            },
        )


def _signature_for(event: AgentEvent) -> Optional[str]:
    """Cross-trace identity of a step. Returns None for events that should
    not contribute to suspiciousness:

      * ``run.start`` / ``run.end`` — trajectory lifecycle markers.
      * ``error`` events emitted by ``agent_name='system'`` — these are the
        synthetic markers from ``AgentDebug.finish_trace(success=False)`` and
        are present in every failed trace; they would saturate suspiciousness
        without revealing anything about the cause.
    """
    et = getattr(event.event_type, 'value', event.event_type)
    if et in {'run.start', 'run.end'}:
        return None
    if et == 'error' and (event.agent_name or '') == 'system':
        return None
    payload = ' '.join(str(event.output or event.input or '').split())[:120]
    return '|'.join([
        str(et),
        str(event.agent_name or ''),
        str(event.module or ''),
        payload,
    ])


def _signatures_for(trajectory: AgentTrajectory) -> set[str]:
    out: set[str] = set()
    for evt in trajectory.events:
        sig = _signature_for(evt)
        if sig:
            out.add(sig)
    return out


def _suspiciousness(
    *, ef: int, ep: int, nf: int, np: int,
    formula: str, dstar_exponent: float,
) -> float:
    import math
    if formula == 'tarantula':
        # Defined as 0 when the step never appears in failing traces.
        if ef == 0:
            return 0.0
        # Defensive: nf>=1 by caller.
        f_rate = ef / nf
        p_rate = (ep / np) if np > 0 else 0.0
        denom = f_rate + p_rate
        return f_rate / denom if denom > 0 else 0.0
    if formula == 'ochiai':
        denom = math.sqrt((ef + ep) * nf)
        return (ef / denom) if denom > 0 else 0.0
    # dstar
    if ep == 0 and (nf - ef) == 0:
        return float('inf') if ef > 0 else 0.0
    denom = ep + (nf - ef)
    if denom == 0:
        return 0.0
    return float((ef ** dstar_exponent) / denom)


@dataclass
class AttributionBudget:
    """Optional cap on ensemble cost."""

    max_backends: Optional[int] = None     # short-circuit after N successful backends
    max_seconds: Optional[float] = None    # walltime cap; remaining backends skipped

    def exceeded(
        self, *, completed: int, elapsed_s: float,
    ) -> bool:
        if self.max_backends is not None and completed >= self.max_backends:
            return True
        if self.max_seconds is not None and elapsed_s >= self.max_seconds:
            return True
        return False


class EnsembleAttributor:
    """Compose any subset of attributors into a single ranked result.

    Two merge strategies:

      * ``"borda"`` (default) — each backend's ranked list contributes
        Borda points (top hypothesis = N, next = N-1, …). Steps are
        ranked by total points × backend weight; ties broken by primary
        confidence × weight.
      * ``"bayesian"`` — combine confidences as 1 - ∏(1 - w_i × c_i)
        across backends that nominated the same (event_id, step_index).
        Treats each backend as an independent noisy classifier; good when
        backends are heterogeneous and confidences are well-calibrated.

    Provenance is HONEST: every output ``Blame.sources`` lists every
    backend that contributed; the rationale aggregates source-by-source
    so the UI can show which backends agreed.

    Pair with ``AttributionBudget`` to cap wall-clock or backend count
    when running expensive backends (e.g., BinarySearch + Counterfactual)
    alongside cheap ones (Heuristic + SBFL).
    """

    id = 'ensemble'

    def __init__(
        self,
        backends: List[Attributor],
        *,
        weights: Optional[Dict[str, float]] = None,
        merge: str = 'borda',
        top_k: int = 5,
        budget: Optional[AttributionBudget] = None,
        fallback: Optional[Attributor] = None,
    ) -> None:
        if not backends:
            raise ValueError('EnsembleAttributor requires at least one backend')
        if merge not in {'borda', 'bayesian'}:
            raise ValueError(
                f"Unknown merge strategy {merge!r}; use 'borda' or 'bayesian'"
            )
        self.backends = list(backends)
        self.weights = weights or {b.id: 1.0 for b in backends}
        self.merge = merge
        self.top_k = top_k
        self.budget = budget
        self.fallback: Attributor = fallback or HeuristicAttributor()

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        import time as _time

        per_backend: List[Tuple[str, AttributionResult]] = []
        started = _time.perf_counter()
        for backend in self.backends:
            if self.budget and self.budget.exceeded(
                completed=len(per_backend),
                elapsed_s=_time.perf_counter() - started,
            ):
                LOG.debug('ensemble budget hit; stopping early')
                break
            try:
                result = backend.attribute(trajectory, findings)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.warning('ensemble backend %s raised: %s', backend.id, exc)
                continue
            per_backend.append((backend.id, result))

        # Any backend produced hypotheses? Otherwise fall back.
        if not any(r.hypotheses for _id, r in per_backend):
            return self.fallback.attribute(trajectory, findings)

        if self.merge == 'borda':
            merged = self._merge_borda(per_backend)
        else:
            merged = self._merge_bayesian(per_backend)

        merged = merged[: self.top_k]
        elapsed_ms = int((_time.perf_counter() - started) * 1000)
        return AttributionResult(
            method=self.id,
            hypotheses=merged,
            elapsed_ms=elapsed_ms,
            raw={
                'merge': self.merge,
                'backends_run': [bid for bid, _ in per_backend],
                'weights': dict(self.weights),
                'budget_exceeded': bool(
                    self.budget
                    and self.budget.exceeded(
                        completed=len(per_backend),
                        elapsed_s=_time.perf_counter() - started,
                    )
                ),
            },
        )

    # ---- merge strategies ----

    def _merge_borda(
        self, per_backend: List[Tuple[str, AttributionResult]],
    ) -> List[Blame]:
        # Aggregator keyed by canonical step identity.
        agg: Dict[Tuple[Optional[str], Optional[int]], _MergeRow] = {}
        for backend_id, result in per_backend:
            w = self.weights.get(backend_id, 1.0)
            n = len(result.hypotheses)
            for rank, h in enumerate(result.hypotheses):
                key = (h.span_id, h.step_index)
                row = agg.setdefault(key, _MergeRow(span_id=h.span_id,
                                                   step_index=h.step_index))
                # Borda points: top = N, next = N-1, … last = 1
                row.borda_points += (n - rank) * w
                row.weighted_conf_sum += w * h.confidence
                row.weight_sum += w
                row.sources.add(backend_id)
                if h.agent_name and not row.agent_name:
                    row.agent_name = h.agent_name
                if h.rationale and backend_id not in row.rationales:
                    row.rationales[backend_id] = h.rationale
                row.evidence.extend(h.evidence)
        ranked = sorted(
            agg.values(),
            key=lambda r: (-r.borda_points, -r.weighted_conf_sum),
        )
        return [self._row_to_blame(r) for r in ranked]

    def _merge_bayesian(
        self, per_backend: List[Tuple[str, AttributionResult]],
    ) -> List[Blame]:
        agg: Dict[Tuple[Optional[str], Optional[int]], _MergeRow] = {}
        for backend_id, result in per_backend:
            w = self.weights.get(backend_id, 1.0)
            for h in result.hypotheses:
                key = (h.span_id, h.step_index)
                row = agg.setdefault(key, _MergeRow(span_id=h.span_id,
                                                   step_index=h.step_index))
                # P(no source supports) *= (1 - w*c)
                effective = max(0.0, min(1.0, w * h.confidence))
                row.bayesian_not_pos *= (1.0 - effective)
                row.weighted_conf_sum += w * h.confidence
                row.weight_sum += w
                row.sources.add(backend_id)
                if h.agent_name and not row.agent_name:
                    row.agent_name = h.agent_name
                if h.rationale and backend_id not in row.rationales:
                    row.rationales[backend_id] = h.rationale
                row.evidence.extend(h.evidence)
        ranked = sorted(
            agg.values(),
            key=lambda r: (-(1.0 - r.bayesian_not_pos), -r.weighted_conf_sum),
        )
        return [self._row_to_blame(r) for r in ranked]

    def _row_to_blame(self, row: '_MergeRow') -> Blame:
        if self.merge == 'borda':
            # Confidence = weighted average of backend confidences (cap at 1).
            conf = min(1.0, row.weighted_conf_sum / max(row.weight_sum, 1e-9))
        else:
            conf = max(0.0, min(1.0, 1.0 - row.bayesian_not_pos))
        rationale = '; '.join(
            f'{bid}: {txt[:140]}' for bid, txt in row.rationales.items()
        ) or 'ensemble agreement'
        return Blame(
            span_id=row.span_id,
            step_index=row.step_index,
            agent_name=row.agent_name,
            confidence=conf,
            rationale=rationale,
            evidence=list(dict.fromkeys(row.evidence)),  # dedupe preserve order
            sources=sorted(row.sources),
        )


@dataclass
class _MergeRow:
    span_id: Optional[str]
    step_index: Optional[int]
    agent_name: Optional[str] = None
    borda_points: float = 0.0
    bayesian_not_pos: float = 1.0
    weighted_conf_sum: float = 0.0
    weight_sum: float = 0.0
    sources: set[str] = field(default_factory=set)
    rationales: Dict[str, str] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)


__all__ = [
    'AllAtOnceAttributor', 'AttributionBudget', 'AttributionResult',
    'Attributor', 'BinarySearchAttributor', 'Blame',
    'CounterfactualAttributor', 'EnsembleAttributor', 'HeuristicAttributor',
    'SBFLAttributor', 'StepByStepAttributor',
]
