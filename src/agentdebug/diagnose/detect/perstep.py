"""Per-step detection, ported from TrajDebug's Stage B.

Both existing LLM detectors judge a *chunk*: they are shown 80 events at once
and asked to return a ranked list of what went wrong. This asks a different
question, once per agent turn --- *is this particular step wrong?* --- with the
rest of the run supplied as history the model may read but not answer about.

That difference is the reason this module exists, and it is the part of
TrajDebug that the first port missed. Measured on SWE-Bench-Pro, adding their
Stage A compression to a chunked detector changed nothing, because compression
in their design is not an accuracy feature: it is what makes ~59 calls per
trajectory affordable. Their tier ladder is defined by *distance from the step
under judgement*, so it only expresses anything when there is a single step
under judgement. Give 80 events one tier each and the gradient is flat, which
is a slightly better truncation and nothing more.

So the ladder finally does something here. Each call renders the focus step in
full, its immediate neighbours at ``th1``, the near band at ``th2``, and the
rest of the history at ``th3`` --- which is a localization prior in its own
right, saying *the answer is around here and this is what led up to it*. Then
the focus advances and the whole gradient is rebuilt around the next step, so
every step gets the spotlight exactly once.

The cost is the honest trade: one call per agent turn instead of one per 80
events, roughly 30x the inference. That is what the compression is for.

A warning carried over from measuring the original: TrajDebug's own Stage B
emitted a trigger on 96.8% of the steps it judged on ALFWorld, which leaves the
downstream clustering to recover one answer from thirty near-identical
accusations. The checklist below is therefore written around explicit negative
clauses --- the cases that look wrong and are not --- because a detector that
fires on everything has not localized anything.

Ported from TrajDebug (THU-KEG/TrajDebug, EMNLP 2026 Findings, MIT),
``detector/stage_b_per_step.py``.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

from agentdebug.diagnose.detect.compression import (
    DEFAULT_OVERALL_CAP_CHARS,
    clip_middle,
    event_header,
    event_text,
    render_history_for_focus,
)
from agentdebug.diagnose.detect.evidence import (
    annotate_quote_verification,
    quote_verification_summary,
)
from agentdebug.diagnose.detect.selection import RootSelector, earliest_finding
from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import (
    SEED_FAILURE_MODES,
    AgentEvent,
    AgentTrajectory,
    ConflictAxis,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
    new_id,
)

LOG = logging.getLogger('agentdebug.detect.perstep')

#: Event kinds that represent something the agent chose to do. Only these are
#: judged: an observation is the environment speaking, and blaming it for the
#: agent's mistake is a category error the conflict axis exists to prevent.
DEFAULT_JUDGABLE_TYPES = frozenset({
    EventType.AGENT_STEP,
    EventType.LLM_RESPONSE,
    EventType.PLAN,
    EventType.REFLECTION,
    EventType.TOOL_CALL,
})

_SYSTEM_PROMPT = """You are AgentDebugX-PerStep. You are shown ONE step of an
agent run, plus the history before it, and you answer a single question: is THIS
step where the run went wrong?

Answer "yes" only if this step commits to something that contradicts a fixed
reference. Quote both, verbatim:

  wrong_content_quote  -- the exact wrong commitment, copied from THIS step.
  reference_quote      -- the exact text it contradicts, copied from wherever
                          conflict_with says it lives.

conflict_with scopes where the reference may come from:
  "task"    -- the stated goal, or a binding rule (must / never / required).
               A role description ("You are a helpful assistant") is NOT binding.
  "context" -- an earlier observation, tool result, or error message.
  "self"    -- this same step, or the agent's OWN earlier plan or reasoning.
               Never a later step: a later step cannot be what this one
               contradicted.
  "env"     -- the tool or environment misbehaved though the agent acted
               correctly.

THESE ARE NOT ERRORS. Most steps are fine, and saying so is the useful answer:
  - exploring, reading files, or gathering information before acting
  - a reasonable attempt that simply did not succeed
  - a step that is only wrong in hindsight, given information that arrives later
  - repeating an action when the previous result justified retrying
  - a step that inherits a mistake made earlier -- blame the step that made it,
    not every step downstream of it
  - being slow, verbose, or inefficient

If you cannot quote a real span for BOTH fields, the answer is "no". A trigger
with an invented or paraphrased quote is worse than no trigger.

confidence is how sure you are that THIS step, and not a neighbouring one, is
the decisive error. Use the range: 0.9+ only when the contradiction is explicit
in the quoted text.

OUTPUT RULES:
1. Output ONLY a JSON object. No prose before or after. No markdown fences.
2. No newlines inside string values.
3. Emit the object COMPLETE.

Schema when the step is fine:
{{"is_error": false}}

Schema when it is not:
{{"is_error": true, "failure_mode_id": "...", "conflict_with": "task"|"context"|"self"|"env",
  "wrong_content_quote": "...", "reference_quote": "...",
  "confidence": 0.0, "reasoning": "<short>"}}

ALLOWED failure_mode_id values:
{modes}
"""


class PerStepAnalyzer:
    """Detect stage component that judges one agent turn per LLM call.

    ``compressions`` is a Stage A pool (see
    :class:`~agentdebug.diagnose.detect.compression.StepCompressor`). Without
    one the history is clipped head-and-tail instead, which still works but
    costs the tier ladder -- the graded rendering is the point, so supplying a
    pool is strongly preferred.
    """

    id = 'perstep'

    def __init__(
        self,
        llm: LLMClient,
        *,
        compressions: Optional[Dict[int, Dict[str, str]]] = None,
        th1_max_distance: int = 2,
        th2_max_distance: int = 5,
        overall_cap_chars: int = DEFAULT_OVERALL_CAP_CHARS,
        focus_max_chars: int = 6000,
        max_tokens: int = 1024,
        max_workers: int = 8,
        judgable_types: Sequence[EventType] = (),
        drop_unsupported: bool = True,
        request_json: bool = True,
        root_selector: Optional[RootSelector] = None,
        quote_similarity: Optional[float] = None,
    ) -> None:
        self.llm = llm
        #: Optional fuzzy floor for quote verification; None keeps it exact.
        self.quote_similarity = quote_similarity
        self.compressions = {int(k): v for k, v in (compressions or {}).items()}
        self.th1_max_distance = th1_max_distance
        self.th2_max_distance = th2_max_distance
        self.overall_cap_chars = overall_cap_chars
        # The focus step is the one thing never compressed: the model is asked
        # to quote out of it, and it cannot quote what it was not shown.
        self.focus_max_chars = focus_max_chars
        self.max_tokens = max_tokens
        self.max_workers = max(1, max_workers)
        self.judgable_types = frozenset(judgable_types) or DEFAULT_JUDGABLE_TYPES
        self.drop_unsupported = drop_unsupported
        self.request_json = request_json
        self.root_selector: RootSelector = root_selector or earliest_finding
        self.stats: Dict[str, int] = {'judged': 0, 'fired': 0, 'parse_failures': 0}

    # -- public API --------------------------------------------------------
    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        events = list(trajectory.events)
        focuses = [
            position for position, event in enumerate(events)
            if self._is_judgable(event)
        ]
        self.stats['judged'] += len(focuses)

        findings: List[FailureFinding] = []
        if focuses:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                results = pool.map(
                    lambda position: self._judge_step(trajectory, events, position),
                    focuses,
                )
            findings = [finding for finding in results if finding is not None]
        self.stats['fired'] += len(findings)

        annotate_quote_verification(
            findings, trajectory, self._shown_text(events), self.quote_similarity
        )
        verification = quote_verification_summary(findings)
        kept = findings
        if self.drop_unsupported:
            kept = [f for f in findings if f.quote_verified is not False]
            dropped = len(findings) - len(kept)
            if dropped:
                LOG.info('dropped %d finding(s) whose quotes did not resolve', dropped)

        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=kept,
            suggestions=self._collect_suggestions(kept),
            metadata={
                'analyzer': self.__class__.__name__,
                'model': self.llm.model,
                'quote_verification': verification,
                'steps_judged': len(focuses),
                'steps_flagged': len(findings),
                # The fire rate is the number to watch. TrajDebug's own Stage B
                # reached 96.8% on ALFWorld, at which point "earliest flagged"
                # and "earliest step" are the same answer.
                'fire_rate': (len(findings) / len(focuses)) if focuses else 0.0,
                'used_compressions': bool(self.compressions),
            },
        )
        root = self.root_selector(kept)
        if root is not None:
            report.root_cause_event_id = root.event_id
            report.root_cause_agent = root.agent_name
            report.root_cause_step_index = root.step_index
            report.summary = (
                f'Likely root cause: {root.failure_mode.name} at step '
                f'{root.step_index} ({len(kept)} of {len(focuses)} steps flagged).'
            )
        else:
            report.summary = f'No failure was detected across {len(focuses)} judged step(s).'
        return report

    # -- internals ---------------------------------------------------------
    def _shown_text(self, events: Sequence[AgentEvent]) -> Dict[str, str]:
        """What the model could actually have quoted, per event.

        Every tier is included rather than the one a particular call happened to
        render: a step appears at th1 when it is the focus's neighbour and th3
        twenty turns later, and a quote is legitimate if it came from any view
        the model was given. Returns empty when there are no compressions, which
        makes verification fall back to the source alone.
        """
        if not self.compressions:
            return {}
        shown: Dict[str, str] = {}
        for position, event in enumerate(events):
            tiers = self.compressions.get(position)
            if not tiers:
                continue
            text = '\n'.join(t for t in tiers.values() if t)
            if text:
                shown[event.event_id] = text
        return shown

    def _is_judgable(self, event: AgentEvent) -> bool:
        try:
            typed = EventType(getattr(event.event_type, 'value', event.event_type))
        except ValueError:
            return False
        return typed in self.judgable_types

    def _judge_step(
        self, trajectory: AgentTrajectory, events: Sequence[AgentEvent], position: int
    ) -> Optional[FailureFinding]:
        history = render_history_for_focus(
            events,
            position,
            self.compressions,
            th1_max_distance=self.th1_max_distance,
            th2_max_distance=self.th2_max_distance,
            overall_cap_chars=self.overall_cap_chars,
            include_focus=False,
            history_only_before=True,
        )
        event = events[position]
        focus = self._render_focus(event, position)

        description = trajectory.metadata.get('agent_framework_description')
        framework_doc = (
            f'HOW TO READ THIS TRAJECTORY:\n{description}\n\n'
            if isinstance(description, str) and description.strip()
            else ''
        )
        user = (
            f'GOAL: {trajectory.goal!r}\n\n'
            f'{framework_doc}'
            f'HISTORY BEFORE THIS STEP (context only -- never your answer):\n'
            f'{history}\n\n'
            f'=== THE STEP YOU ARE JUDGING ===\n{focus}\n'
        )
        modes_doc = '\n'.join(
            f'- {mode_id}: {mode.description}'
            for mode_id, mode in SEED_FAILURE_MODES.items()
        )
        messages = [
            {'role': 'system', 'content': _SYSTEM_PROMPT.format(modes=modes_doc)},
            {'role': 'user', 'content': user},
        ]
        kwargs: Dict[str, Any] = {'max_tokens': self.max_tokens}
        if self.request_json:
            kwargs['response_format'] = {'type': 'json_object'}
        try:
            result = self.llm.complete(messages=messages, **kwargs)
        except Exception:
            LOG.warning('per-step call failed at position %d', position, exc_info=True)
            self.stats['parse_failures'] += 1
            return None

        parsed = extract_json_block(result.text)
        if not isinstance(parsed, dict):
            self.stats['parse_failures'] += 1
            LOG.debug('per-step returned no JSON; raw=%r', (result.text or '')[:200])
            return None
        if not parsed.get('is_error'):
            return None
        return self._to_finding(parsed, event, position)

    def _render_focus(self, event: AgentEvent, position: int) -> str:
        body = clip_middle(event_text(event), self.focus_max_chars)
        return f'{event_header(event, position)}\n{body}'

    def _to_finding(
        self, parsed: Dict[str, Any], event: AgentEvent, position: int
    ) -> Optional[FailureFinding]:
        mode_id = str(parsed.get('failure_mode_id') or '')
        failure_mode = SEED_FAILURE_MODES.get(mode_id)
        if failure_mode is None:
            # An unrecognised label with real quotes is still a located error,
            # so keep the finding and let the taxonomy be the uncertain part.
            failure_mode = FailureMode(
                mode_id='unknown.unlabeled',
                name='Unlabeled failure',
                family='unknown',
                description='The detector located a step but named a mode outside the taxonomy.',
            )
        wrong = self._opt_str(parsed.get('wrong_content_quote'))
        reference = self._opt_str(parsed.get('reference_quote'))
        if not wrong or not reference:
            # The prompt makes both mandatory; without them there is nothing to
            # verify, and an unverifiable finding is the thing this avoids.
            return None
        return FailureFinding(
            finding_id=new_id('finding'),
            failure_mode=failure_mode,
            event_id=event.event_id,
            agent_name=event.agent_name,
            step_index=event.step_index if event.step_index is not None else position,
            confidence=self._coerce_float(parsed.get('confidence'), default=0.5),
            evidence=[q for q in (wrong, reference) if q],
            wrong_content_quote=wrong,
            reference_quote=reference,
            conflict_with=self._to_axis(parsed.get('conflict_with')),
            suggestion=self._suggestion(failure_mode),
            metadata={
                'source': 'perstep',
                'analysis_layer': 'perstep',
                'finding_source_label': 'Per-step judge',
                'trigger_scope': 'step',
                'trigger_reason': self._opt_str(parsed.get('reasoning')) or '',
                'why_reported': (
                    f'The step was judged on its own, against the history before it, '
                    f'and mapped to {failure_mode.mode_id}.'
                ),
            },
        )

    @staticmethod
    def _to_axis(value: Any) -> Optional[ConflictAxis]:
        try:
            return ConflictAxis(str(value))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _opt_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _suggestion(failure_mode: FailureMode) -> Optional[str]:
        if not failure_mode.suggestion_templates:
            return None
        return str(failure_mode.suggestion_templates[0])

    def _collect_suggestions(self, findings: Sequence[FailureFinding]) -> List[str]:
        seen = set()
        out: List[str] = []
        for finding in findings:
            if finding.suggestion and finding.suggestion not in seen:
                seen.add(finding.suggestion)
                out.append(finding.suggestion)
        return out


__all__ = ['DEFAULT_JUDGABLE_TYPES', 'PerStepAnalyzer']
