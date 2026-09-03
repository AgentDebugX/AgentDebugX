"""Evidence-grounded step detection, ported from TrajDebug's Stage B.

The existing :class:`~agentdebug.diagnose.detect.judge.LLMJudgeAnalyzer` asks a
model for a label and a confidence. This asks for a label *and the two spans of
text that justify it*: what the step committed to, and what that commitment
contradicts. Every finding is then checked against the trajectory, so a
fabricated citation is caught by string search rather than trusted.

Three differences from the base judge, all of them the reason this exists:

1. **A verbatim pair is mandatory.** ``wrong_content_quote`` must come from the
   blamed step; ``reference_quote`` must come from whatever it violates.
2. **A conflict axis scopes the reference.** ``task`` / ``context`` / ``self`` /
   ``env`` says *where* the reference quote is allowed to come from, which is
   what makes requirement 1 checkable rather than decorative. Without it,
   "quote something that disagrees" is satisfiable by quoting anything.
3. **The model is told what the trace format means.** The base judge passes
   ``FRAMEWORK: 'claude_code'`` -- a bare label. Where a trajectory carries an
   ``agent_framework_description`` (the TrajDebug importer preserves theirs, and
   any importer can set one), it is included, so the model knows whether a
   ``user`` message is a new instruction or an environment observation. One
   prompt then handles trace shapes that otherwise need one prompt each.

Findings whose quotes do not resolve are dropped by default, because a consumer
cannot distinguish them from grounded ones once they leave here. The counts are
always recorded on the report either way, so the grounding rate stays
measurable -- that ratio is a hallucination rate for the detector, which this
library previously had no way to state about its own output.

Ported from TrajDebug (THU-KEG/TrajDebug, EMNLP 2026 Findings, MIT), whose
Stage B introduced the verbatim-pair requirement and the cat-1/2/3/env
categories this calls ``conflict_with``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

LOG = logging.getLogger('agentdebug.detect.trajdebug')

_SYSTEM_PROMPT = """You are AgentDebugX-TrajDebug, locating the steps where an
agent run went wrong.

For each step that is wrong, emit one trigger. A trigger is ONLY valid if you
quote, verbatim, BOTH of these from the trajectory you were given:

  wrong_content_quote  -- the exact text of the wrong commitment, copied from
                          the step you are blaming and from no other step.
  reference_quote      -- the exact text of what it contradicts.

Copy these character for character. Do not paraphrase, summarise, translate, or
repair them. If you cannot find a real span of text for both, do not emit the
trigger at all. A trigger with an invented quote is worse than no trigger.

Also set conflict_with, which says where reference_quote must come from:
  "task"    -- the stated goal, or a binding rule in a system message. Binding
               means must / shall / required / never / forbidden. A plain role
               description ("You are a helpful agent") is NOT binding.
  "context" -- an earlier observation, tool result, or error message.
  "self"    -- the same step, or the agent's OWN earlier plan or reasoning.
               Never a later step: a later step cannot be what an earlier one
               contradicted.
  "env"     -- the environment or tool is at fault, not the agent.

Be conservative. Most steps are fine. Cap the triggers array at {max_findings}
entries and pick the most decisive.

CRITICAL OUTPUT RULES:
1. Output ONLY a JSON object. No prose before or after. No markdown fences.
2. Do NOT include newlines inside string values.
3. Emit the JSON object COMPLETE -- never stop mid-key or mid-array.

Schema (fields in this order):
{{"triggers":[{{"event_id":"...", "step_index":N, "agent_name":"...",
  "failure_mode_id":"...", "conflict_with":"task"|"context"|"self"|"env",
  "wrong_content_quote":"...", "reference_quote":"...",
  "confidence":0..1, "confidence_reasoning":"<short>"}}, ...],
  "summary":"<short>"}}
"""


class TrajDebugAnalyzer:
    """Detect stage component producing evidence-grounded findings."""

    id = 'trajdebug'

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_events_per_call: int = 60,
        max_event_chars: int = 3000,
        max_tokens: int = 8192,
        max_findings_per_chunk: int = 8,
        drop_unsupported: bool = True,
        context_builder: Optional[Any] = None,
        root_selector: Optional[RootSelector] = None,
        request_json: bool = False,
    ) -> None:
        self.llm = llm
        self.max_events_per_call = max_events_per_call
        # Much larger than the base judge's 300-char evidence budget, on
        # purpose: the model is being asked to copy spans out of this text, so
        # truncating aggressively cuts the passages it needs to quote and shows
        # up downstream as a grounding failure rather than as truncation. Real
        # ALFWorld steps run past 5k characters; at 1200 the first live run
        # produced quotes that ran off the end of what the model could see.
        self.max_event_chars = max_event_chars
        self.max_tokens = max_tokens
        self.max_findings_per_chunk = max_findings_per_chunk
        #: Drop findings whose quotes do not resolve. Counts are recorded on the
        #: report regardless, so turning this off measures rather than trusts.
        self.drop_unsupported = drop_unsupported
        # Both default to the historical behaviour. `context_builder` is any
        # object with `render_chunk(events, chunk)`; see
        # agentdebug.diagnose.detect.compression.GradedContextBuilder. It
        # matters more here than for the base judge: a detector required to
        # quote verbatim can only quote what its context actually contains.
        self.context_builder = context_builder
        self.root_selector: RootSelector = root_selector or earliest_finding
        # Ask the provider to constrain the response to JSON. Off by default;
        # see the note on LLMJudgeAnalyzer.request_json. A chunk that fails to
        # parse yields no triggers, which downstream cannot tell apart from a
        # chunk the model found nothing wrong with.
        self.request_json = request_json

    # -- public API --------------------------------------------------------
    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        findings: List[FailureFinding] = []
        summaries: List[str] = []

        for chunk in self._chunk_events(trajectory.events):
            chunk_findings, summary = self._detect_chunk(trajectory, chunk)
            findings.extend(chunk_findings)
            if summary:
                summaries.append(summary)

        annotate_quote_verification(findings, trajectory)
        verification = quote_verification_summary(findings)

        kept = findings
        if self.drop_unsupported:
            kept = [f for f in findings if f.quote_verified is not False]
            dropped = len(findings) - len(kept)
            if dropped:
                LOG.info(
                    'dropped %d finding(s) whose quotes did not resolve against the trajectory',
                    dropped,
                )

        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=kept,
            suggestions=self._collect_suggestions(kept),
            metadata={
                'analyzer': self.__class__.__name__,
                'model': self.llm.model,
                # The ratio of verified to (verified + unsupported) is this
                # detector's measured grounding rate on this trajectory.
                'quote_verification': verification,
                'dropped_unsupported': self.drop_unsupported,
            },
        )

        root = self._select_root(kept)
        report.summary = ' '.join(s for s in summaries if s) or (
            f'Likely root cause: {root.failure_mode.name}'
            f' at step {root.step_index}.'
            if root
            else 'No evidence-grounded failure was detected.'
        )
        if root is not None:
            report.root_cause_event_id = root.event_id
            report.root_cause_agent = root.agent_name
            report.root_cause_step_index = root.step_index
        return report

    # -- internals ---------------------------------------------------------
    def _chunk_events(self, events: Sequence[AgentEvent]) -> List[List[AgentEvent]]:
        if not events:
            return []
        return [
            list(events[i : i + self.max_events_per_call])
            for i in range(0, len(events), self.max_events_per_call)
        ]

    def _detect_chunk(
        self, trajectory: AgentTrajectory, chunk: List[AgentEvent]
    ) -> Tuple[List[FailureFinding], str]:
        system = _SYSTEM_PROMPT.format(max_findings=self.max_findings_per_chunk)
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': self._render_user_prompt(trajectory, chunk)},
        ]
        kwargs: Dict[str, Any] = {'max_tokens': self.max_tokens}
        if self.request_json:
            kwargs['response_format'] = {'type': 'json_object'}
        result = self.llm.complete(messages=messages, **kwargs)
        parsed = extract_json_block(result.text)
        if not parsed:
            LOG.warning('trajdebug detector returned no JSON; raw=%r', result.text[:300])
            return [], ''

        findings: List[FailureFinding] = []
        for raw in parsed.get('triggers') or []:
            finding = self._finding_from_trigger(raw)
            if finding is not None:
                findings.append(finding)
        return findings, str(parsed.get('summary') or '')

    def _finding_from_trigger(self, raw: Any) -> Optional[FailureFinding]:
        if not isinstance(raw, dict):
            return None

        failure_mode = SEED_FAILURE_MODES.get(str(raw.get('failure_mode_id') or ''))
        if failure_mode is None:
            return None

        wrong = self._coerce_str(raw.get('wrong_content_quote'))
        reference = self._coerce_str(raw.get('reference_quote'))
        if not (wrong and wrong.strip()) or not (reference and reference.strip()):
            # The prompt says a trigger without both quotes should not be
            # emitted. Honouring that here keeps an unquoted guess from
            # entering the report as an unchecked (quote_verified=None)
            # finding, which is the state reserved for detectors that do not
            # participate in grounding at all.
            LOG.debug('discarded trigger missing one or both quotes')
            return None

        return FailureFinding(
            finding_id=new_id('finding'),
            failure_mode=failure_mode,
            event_id=self._coerce_str(raw.get('event_id')),
            agent_name=self._coerce_str(raw.get('agent_name')),
            step_index=self._coerce_int(raw.get('step_index')),
            confidence=self._coerce_float(raw.get('confidence'), default=0.5),
            evidence=[e for e in (wrong, reference) if e],
            wrong_content_quote=wrong,
            reference_quote=reference,
            conflict_with=self._coerce_conflict(raw.get('conflict_with')),
            suggestion=self._suggestion(failure_mode),
            metadata={
                'source': 'trajdebug',
                'analysis_layer': 'trajdebug',
                'finding_source_label': 'TrajDebug evidence-grounded detector',
                'trigger_scope': 'event',
                'confidence_reasoning': self._coerce_str(raw.get('confidence_reasoning')),
                'why_reported': (
                    'The detector quoted the wrong commitment and the text it '
                    'contradicts, and both quotes were checked against the trajectory.'
                ),
            },
        )

    def _render_user_prompt(
        self, trajectory: AgentTrajectory, chunk: List[AgentEvent]
    ) -> str:
        modes_doc = '\n'.join(
            f'- {mode_id}: {mode.description}'
            for mode_id, mode in SEED_FAILURE_MODES.items()
        )
        if self.context_builder is not None:
            events_doc = self.context_builder.render_chunk(trajectory.events, chunk)
        else:
            events_doc = '\n'.join(self._render_event(evt) for evt in chunk)

        # A prose brief on what this trace format's roles mean, when the
        # importer supplied one. Without it the model must guess whether a
        # `user` message is a new instruction or an environment observation,
        # and it guesses differently across formats.
        description = trajectory.metadata.get('agent_framework_description')
        framework_doc = (
            f'HOW TO READ THIS TRAJECTORY:\n{description}\n\n'
            if isinstance(description, str) and description.strip()
            else ''
        )

        return (
            f'GOAL: {trajectory.goal!r}\n'
            f'FRAMEWORK: {trajectory.framework!r}\n\n'
            f'{framework_doc}'
            f'ALLOWED FAILURE MODES:\n{modes_doc}\n\n'
            f'TRAJECTORY:\n{events_doc}\n'
        )

    def _render_event(self, event: AgentEvent) -> str:
        def shorten(value: Any) -> str:
            text = '' if value is None else str(value)
            if len(text) > self.max_event_chars:
                text = text[: self.max_event_chars] + '…'
            return text

        parts = [
            f'[step {event.step_index}]',
            f'event_id={event.event_id}',
            f'type={self._event_type_value(event.event_type)}',
            f'agent={event.agent_name}',
        ]
        for label, value in (
            ('input', event.input),
            ('output', event.output),
            ('error', event.error),
        ):
            rendered = shorten(value)
            if rendered:
                parts.append(f'{label}={rendered}')
        return ' '.join(parts)

    def _select_root(
        self, findings: List[FailureFinding]
    ) -> Optional[FailureFinding]:
        return self.root_selector(findings)

    def _collect_suggestions(self, findings: List[FailureFinding]) -> List[str]:
        seen = set()
        out: List[str] = []
        for finding in findings:
            if finding.suggestion and finding.suggestion not in seen:
                seen.add(finding.suggestion)
                out.append(finding.suggestion)
        return out

    @staticmethod
    def _suggestion(failure_mode: FailureMode) -> Optional[str]:
        if not failure_mode.suggestion_templates:
            return None
        return str(failure_mode.suggestion_templates[0])

    @staticmethod
    def _coerce_conflict(value: Any) -> Optional[ConflictAxis]:
        if not isinstance(value, str):
            return None
        try:
            return ConflictAxis(value.strip().lower())
        except ValueError:
            return None

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
    def _event_type_value(event_type: EventType) -> str:
        value = getattr(event_type, 'value', event_type)
        return value if isinstance(value, str) else str(value)


__all__ = ['TrajDebugAnalyzer']
