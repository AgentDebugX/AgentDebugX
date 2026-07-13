"""LLM-based judge analyzer.

Walks an ``AgentTrajectory`` and asks an LLM to label step-level failures
against the 19 seed modes shipped in :mod:`agentdebug.taxonomy`. The judge is
intentionally schemed to the existing taxonomy so the rest of the pipeline
(``DiagnosticReport``, recovery, attribution) doesn't need a parallel label
space.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
    confidence_or_default,
    new_id,
)
from agentdebug.schema import SEED_FAILURE_MODES

LOG = logging.getLogger('agentdebug.judges')

_SYSTEM_PROMPT = """You are AgentDebugX-Judge, an expert at diagnosing failures
in LLM agent trajectories.

You will be given:
* the agent's goal,
* a list of ALLOWED failure mode codes with descriptions,
* the chronological events of one agent run.

Your job: identify each step where a failure occurred and label it with ONE of
the allowed failure mode codes. Be conservative — only flag steps where the
evidence in the event payload supports the label. If the trajectory contains no
failure, return an empty findings list.

CRITICAL OUTPUT RULES (these maximize the chance your reply parses):
1. Output ONLY a JSON object. No prose before/after. No markdown fences.
2. Cap the findings array at {max_findings} entries — pick the most important.
3. Keep each "evidence" entry under 120 characters; keep each "rationale" /
   "summary" under 200 characters.
4. Do NOT include newlines inside string values.
5. Emit the JSON object COMPLETE — never stop mid-key or mid-array.

Schema (compact — fields in this order):
{{"findings":[{{"event_id":"...", "step_index":N|null, "agent_name":"...",
  "failure_mode_id":"...", "confidence":0..1, "evidence":["..."]}}, ...],
  "summary":"<short>"}}
"""


class LLMJudgeAnalyzer:
    """LLM-as-judge analyzer schemed to the 19 seed failure modes."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_events_per_call: int = 80,
        max_evidence_chars: int = 300,
        max_tokens: int = 8192,
        max_findings_per_chunk: int = 6,
    ) -> None:
        self.llm = llm
        self.max_events_per_call = max_events_per_call
        self.max_evidence_chars = max_evidence_chars
        # NOTE: thinking models (Gemini 2.x/3.x, o-series) spend a substantial
        # fraction of `max_tokens` on reasoning tokens before any text is
        # emitted. 8192 is the safe default after the v0.2.6 Who&When debate-
        # trace observation that 4096 truncated mid-array on long traces.
        self.max_tokens = max_tokens
        # The system prompt asks the model to cap its findings array so the
        # JSON closes even when many candidate failures exist. Reuse the prompt
        # placeholder for this cap.
        self.max_findings_per_chunk = max_findings_per_chunk

    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        events = trajectory.events
        findings: List[FailureFinding] = []
        summary_parts: List[str] = []
        novel_candidates: List[Dict[str, Any]] = []
        for chunk in self._chunk_events(events):
            findings_chunk, summary, novel = self._judge_chunk(trajectory, chunk)
            findings.extend(findings_chunk)
            novel_candidates.extend(novel)
            if summary:
                summary_parts.append(summary)
        root = self._select_root(findings)
        metadata: Dict[str, Any] = {
            'analyzer': self.__class__.__name__, 'model': self.llm.model,
        }
        if novel_candidates:
            # Surfaced (not dropped) so TaxonomyInducer can harvest them.
            metadata['novel_mode_candidates'] = novel_candidates
        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=findings,
            suggestions=self._collect_suggestions(findings),
            metadata=metadata,
        )
        report.summary = (
            ' '.join(s for s in summary_parts if s)
            or (
                f'Likely root cause: {root.failure_mode.name}'
                f' in {root.agent_name or "unknown agent"}'
                f' at step {root.step_index}.'
                if root
                else 'No failure was detected.'
            )
        )
        if root is not None:
            report.root_cause_event_id = root.event_id
            report.root_cause_agent = root.agent_name
            report.root_cause_step_index = root.step_index
        return report

    def _chunk_events(self, events: Sequence[AgentEvent]) -> List[List[AgentEvent]]:
        if not events:
            return []
        return [
            list(events[i : i + self.max_events_per_call])
            for i in range(0, len(events), self.max_events_per_call)
        ]

    def _judge_chunk(
        self, trajectory: AgentTrajectory, chunk: List[AgentEvent]
    ) -> tuple[List[FailureFinding], str, List[Dict[str, Any]]]:
        user = self._render_user_prompt(trajectory, chunk)
        # Inject the max_findings cap into the system prompt at format time so
        # we can tune it per-call without forking the prompt.
        system = _SYSTEM_PROMPT.format(max_findings=self.max_findings_per_chunk)
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        result = self.llm.complete(messages=messages, max_tokens=self.max_tokens)
        parsed = extract_json_block(result.text)
        if not parsed:
            LOG.warning('LLM judge returned no JSON; raw=%r', result.text[:300])
            return [], '', []
        raw_findings = parsed.get('findings') or []
        summary = str(parsed.get('summary') or '')
        findings: List[FailureFinding] = []
        novel: List[Dict[str, Any]] = []
        for raw in raw_findings:
            if not isinstance(raw, dict):
                continue
            mode_id = str(raw.get('failure_mode_id') or '')
            failure_mode = SEED_FAILURE_MODES.get(mode_id)
            if failure_mode is None:
                # The judge wanted a label outside the seed set. Rather than
                # drop it, record a novel-mode candidate so taxonomy induction
                # can cluster these later and propose new modes for review.
                if mode_id:
                    novel.append({
                        'failure_mode_id': mode_id,
                        'description': self._coerce_str(raw.get('description'))
                        or self._coerce_str(raw.get('rationale')) or mode_id,
                        'agent_name': self._coerce_str(raw.get('agent_name')),
                        'step_index': self._coerce_int(raw.get('step_index')),
                        'evidence': self._coerce_str_list(raw.get('evidence')),
                    })
                    LOG.debug('captured novel failure_mode_id from judge: %s', mode_id)
                continue
            findings.append(
                FailureFinding(
                    finding_id=new_id('finding'),
                    failure_mode=failure_mode,
                    event_id=self._coerce_str(raw.get('event_id')),
                    agent_name=self._coerce_str(raw.get('agent_name')),
                    step_index=self._coerce_int(raw.get('step_index')),
                    confidence=self._coerce_float(raw.get('confidence'), default=0.5),
                    evidence=self._coerce_str_list(raw.get('evidence')),
                    suggestion=self._suggestion(failure_mode),
                    metadata={
                        'source': 'llm_judge',
                        'analysis_layer': 'llm_judge',
                        'finding_source_label': 'LLM judge',
                        'trigger_scope': 'event',
                        'trigger_reason': 'The judge model labeled this step from the trajectory context.',
                        'why_reported': (
                            f'The LLM judge mapped this step to {failure_mode.mode_id} '
                            f'based on the event payload and surrounding trajectory.'
                        ),
                    },
                )
            )
        return findings, summary, novel

    def _render_user_prompt(
        self, trajectory: AgentTrajectory, chunk: List[AgentEvent]
    ) -> str:
        modes_doc = '\n'.join(
            f'- {mode_id}: {mode.description}'
            for mode_id, mode in SEED_FAILURE_MODES.items()
        )
        events_doc = '\n'.join(self._render_event(evt) for evt in chunk)
        return (
            f'GOAL: {trajectory.goal!r}\n'
            f'FRAMEWORK: {trajectory.framework!r}\n\n'
            f'ALLOWED FAILURE MODES:\n{modes_doc}\n\n'
            f'EVENTS:\n{events_doc}\n'
        )

    def _render_event(self, event: AgentEvent) -> str:
        def shorten(value: Any) -> str:
            text = '' if value is None else str(value)
            if len(text) > self.max_evidence_chars:
                text = text[: self.max_evidence_chars] + '…'
            return text

        return (
            f'event_id={event.event_id} '
            f'type={self._event_type_value(event.event_type)} '
            f'agent={event.agent_name} '
            f'module={event.module} step={event.step_index} '
            f'input={shorten(event.input)} '
            f'output={shorten(event.output)} '
            f'error={shorten(event.error)} '
            f'metadata={shorten(event.metadata)}'
        )

    def _select_root(
        self, findings: List[FailureFinding]
    ) -> Optional[FailureFinding]:
        if not findings:
            return None
        return sorted(
                findings,
                key=lambda finding: (
                    finding.step_index is None,
                    finding.step_index if finding.step_index is not None else 10**9,
                    -confidence_or_default(finding.confidence),
                ),
        )[0]

    def _collect_suggestions(self, findings: List[FailureFinding]) -> List[str]:
        seen = set()
        out: List[str] = []
        for f in findings:
            if f.suggestion and f.suggestion not in seen:
                seen.add(f.suggestion)
                out.append(f.suggestion)
        return out

    def _suggestion(self, failure_mode: FailureMode) -> Optional[str]:
        if not failure_mode.suggestion_templates:
            return None
        return str(failure_mode.suggestion_templates[0])

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
    def _event_type_value(event_type: EventType) -> str:
        value = getattr(event_type, 'value', event_type)
        if isinstance(value, str):
            return value
        return str(value)


__all__ = ['LLMJudgeAnalyzer']
