"""DeepDebug diagnosis profile.

DeepDebug owns the complete high-cost Diagnose workflow: global analysis,
structure-guided localization, candidate adjudication, and evidence-backed fix
guidance. It uses the ``aao_moe`` attribution method
(:mod:`agentdebug.diagnose.attribute.moe`) internally:

  * Two experts produce candidates — ``all_at_once`` (whole-trace single shot)
    and a structure-gated MoE (multi-agent -> root-seeking cascade; single-agent
    -> neutral bisection + full-context endgame).
  * The *refine* step is an agreement arbitration: if the two experts pick the
    SAME step, that is the root cause; if they disagree, the model picks between
    the two candidates shown with their +-1 context windows (short prompt).

This beats BinarySearch on Who&When (both Qwen models) and on AgentErrorBench-9B
/ Gemini-AEB, conceding only the strong-model + no-ground-truth AEB-27B cell.
Runs at temperature 0; it never re-executes the agent.

Each run records :class:`DeepDebugRound` entries so the diagnosis stays
auditable. The profile is read-only and never re-executes the original agent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agentdebug.diagnose.attribute.moe import (
    AaoMoeAnalysis,
    analyze_aao_moe,
    resolve_candidate_event,
)
from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticAuditEntry,
    DiagnosticReport,
    FailureFinding,
    FailureMode,
)
from agentdebug.diagnose.attribute.deep_memory import (
    DeepMemoryStore,
    MemoryReference,
    NullMemoryStore,
)

LOG = logging.getLogger('agentdebug.deep')

_REFINE_PROMPT = (
    'You are AgentDebugX-DeepDebug writing the final, human-readable diagnosis. '
    'The decisive ROOT-CAUSE step has ALREADY been localized for you -- do NOT '
    'second-guess or move it. Using the step and its surrounding context, write: '
    'a one-or-two sentence diagnosis of WHY that step caused the failure; the '
    'concrete evidence (short quoted snippets from the shown steps); and ONE '
    'concrete, actionable fix.\n'
    'Every evidence quote MUST be copied verbatim from the shown event and cite '
    'that event_id. Do not paraphrase or invent evidence. '
    'Respond ONLY with JSON: {"summary": "<1-2 sentences>", '
    '"evidence": [{"event_id": "<shown event id>", '
    '"quote": "<verbatim quote>"}, ...], "suggestion": "<one fix>"}'
)


@dataclass
class DeepDebugRound:
    """One recorded step of the analysis, for audit."""

    name: str
    request_summary: str
    response_summary: str
    duration_ms: int
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeepDebugResult:
    report: DiagnosticReport
    rounds: List[DeepDebugRound]
    hypotheses: List[Any] = field(default_factory=list)
    analysis: Optional[AaoMoeAnalysis] = None
    diagnosis: Optional['DeepDebugDiagnosis'] = None

    # Proxy the common DiagnosticReport fields so a DeepDebugResult can be used
    # interchangeably with a single-shot DiagnosticReport.
    @property
    def root_cause_step_index(self) -> Optional[int]:
        return self.report.root_cause_step_index

    @property
    def root_cause_agent(self) -> Optional[str]:
        return self.report.root_cause_agent

    @property
    def findings(self) -> List[FailureFinding]:
        return self.report.findings

    @property
    def summary(self) -> Optional[str]:
        return self.report.summary


@dataclass(frozen=True)
class DeepDebugDiagnosis:
    """Evidence-backed diagnosis produced after the root step is fixed."""

    summary: str
    evidence: List[str]
    suggestion: str
    duration_ms: int
    evidence_references: List['DeepDebugEvidence'] = field(default_factory=list)
    rejected_evidence_count: int = 0


@dataclass(frozen=True)
class DeepDebugEvidence:
    """A quote verified against one concrete trajectory event."""

    event_id: str
    quote: str


class DeepDebugAnalyzer:
    """Complete diagnosis profile backed by the ``aao_moe`` localizer.

    AllAtOnce + structure-gated MoE, with a +-1-window agreement arbitration as
    the refine step. Beats BinarySearch on Who&When (both Qwen models) and
    AEB-9B / Gemini-AEB; ties/concedes AEB-27B. Never re-executes the agent.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        memory_store: Optional[DeepMemoryStore] = None,
        use_memory: bool = False,
        use_ground_truth_context: bool = False,
        label_hint: str = '',
        candidate_labels: Optional[List[str]] = None,
        ctx_before: int = 1,
        ctx_after: int = 1,
        prior_findings: Optional[List[Any]] = None,
        **_legacy_ignored: Any,
    ) -> None:
        self.llm = llm
        # Memory is an opt-in switch. Default = NullMemoryStore (no retrieval,
        # no sqlite side effects). ``use_memory`` turns on top-1 historical
        # retrieval that is fed to both experts + the refine step.
        self.memory_store = memory_store or NullMemoryStore()
        self.use_memory = use_memory
        self.use_ground_truth_context = use_ground_truth_context
        self.label_hint = label_hint
        self.candidate_labels = candidate_labels
        self.ctx_before = ctx_before
        self.ctx_after = ctx_after
        # Design gap G5: upstream detector/judge findings, injected into both
        # experts and the refine turn as prior signals (explicitly fallible).
        # Default None = byte-identical behavior to the pre-G5 analyzer.
        self.prior_findings = prior_findings

    def analyze(self, trajectory: AgentTrajectory) -> DeepDebugResult:
        """Localize the decisive failure step, then write a readable diagnosis.

        Pipeline: (0) optional top-1 historical retrieval -> (1) global read ->
        (2) structure-guided probe -> (3) cross-examination -> (4) diagnosis
        and suggestion. The final stage cannot move the localized root step.
        """
        # Step 0: optional top-1 historical retrieval (opt-in via use_memory).
        ref, reference_hint = self._retrieve_reference(trajectory)
        findings_hint = self._render_prior_findings()
        if findings_hint:
            reference_hint = (
                f'{reference_hint}\n\n{findings_hint}' if reference_hint
                else findings_hint
            )

        # Steps 1-3: global read, structure-guided probe, cross-examination.
        analysis = analyze_aao_moe(
            trajectory, llm=self.llm, label_hint=self.label_hint,
            candidate_labels=self.candidate_labels, ctx_before=self.ctx_before,
            ctx_after=self.ctx_after, use_ground_truth_context=self.use_ground_truth_context,
            reference_hint=reference_hint)
        root = analysis.adjudication.candidate
        root_ev = resolve_candidate_event(
            list(trajectory.events),
            event_id=root.event_id,
            step_index=root.step_index,
            agent_name=root.agent_name,
        )
        if root_ev is None:
            raise ValueError(
                'DeepDebug could not ground the localized root cause to a '
                'unique trajectory event'
            )
        root_step = root_ev.step_index if root_ev else root.step_index
        root_agent = root_ev.agent_name if root_ev else root.agent_name
        root_eid = root_ev.event_id if root_ev else root.event_id

        # Step 4: human-readable summary / evidence / suggestion for
        # the localized step (the located step itself is fixed, not re-decided).
        refine_started = time.perf_counter()
        refined = self._refine(trajectory, root_ev, root_step, root_agent, reference_hint)
        summary_text = str(refined.get('summary') or '').strip()
        evidence_payload = refined.get('evidence')
        raw_evidence = (
            list(evidence_payload)
            if isinstance(evidence_payload, list)
            else [evidence_payload] if evidence_payload else []
        )
        evidence_references = self._validate_evidence(trajectory, raw_evidence)
        rejected_evidence_count = len(raw_evidence) - len(evidence_references)
        suggestion = str(refined.get('suggestion') or '').strip()
        if not evidence_references and root_ev is not None:
            fallback = self._fallback_evidence(root_ev)
            if fallback is not None:
                evidence_references = [fallback]
        if not summary_text:
            summary_text = (
                f'The decision at step {root_step} by {root_agent} introduced '
                'the earliest error that led to the failed outcome.'
            )
        if not suggestion:
            suggestion = self._fallback_suggestion(root_ev)
        evidence = [reference.quote for reference in evidence_references]
        diagnosis = DeepDebugDiagnosis(
            summary=summary_text,
            evidence=evidence,
            suggestion=suggestion,
            duration_ms=int((time.perf_counter() - refine_started) * 1000),
            evidence_references=evidence_references,
            rejected_evidence_count=rejected_evidence_count,
        )

        # One finding so the cascade / --traceback view renders the located root.
        findings: List[FailureFinding] = []
        if root_step is not None:
            findings = [FailureFinding(
                failure_mode=FailureMode(
                    mode_id='attribution.root_cause',
                    name='Localized root cause',
                    family='attribution',
                    description='Decisive error step localized by the AllAtOnce+MoE attributor.'),
                event_id=root_eid,
                agent_name=root_agent,
                step_index=root_step,
                confidence=root.confidence,
                evidence=evidence,
                suggestion=suggestion or None,
            )]

        summary = summary_text or (
            f'DeepDebug cross-examination ({analysis.adjudication.verdict}): '
            f'root cause at step {root_step} ({root_agent}).')

        rounds = [
            DeepDebugRound(
                name='global_read',
                request_summary='read the complete trajectory and propose candidate A',
                response_summary=(
                    f'step={analysis.global_read.step_index} '
                    f'agent={analysis.global_read.agent_name}'
                ),
                duration_ms=analysis.global_read.duration_ms,
                payload=asdict(analysis.global_read),
            ),
            DeepDebugRound(
                name='structure_probe',
                request_summary=(
                    f'{analysis.structure_probe.strategy} investigation'
                    f'{" + memory" if reference_hint else ""}'
                ),
                response_summary=(
                    f'step={analysis.structure_probe.candidate.step_index} '
                    f'agent={analysis.structure_probe.candidate.agent_name} '
                    f'after {len(analysis.structure_probe.decisions)} decisions'
                ),
                duration_ms=analysis.structure_probe.candidate.duration_ms,
                payload=asdict(analysis.structure_probe),
            ),
            DeepDebugRound(
                name='cross_examine',
                request_summary=(
                    f'compare steps {analysis.adjudication.compared_steps}'
                ),
                response_summary=(
                    f'verdict={analysis.adjudication.verdict} -> '
                    f'step={root_step} agent={root_agent}'
                ),
                duration_ms=analysis.adjudication.duration_ms,
                payload=asdict(analysis.adjudication),
            ),
            DeepDebugRound(
                name='diagnose_and_suggest',
                request_summary=f'diagnose fixed step {root_step} ({root_agent})',
                response_summary=(summary_text or '(template summary)')[:200],
                duration_ms=diagnosis.duration_ms,
                payload=asdict(diagnosis),
            ),
        ]
        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=findings,
            suggestions=[suggestion] if suggestion else [],
            summary=summary,
            root_cause_event_id=root_eid,
            root_cause_agent=root_agent,
            root_cause_step_index=root_step,
            attribution={
                'method': 'deepdebug',
                'primary': {
                    'span_id': root_eid,
                    'step_index': root_step,
                    'agent_name': root_agent,
                    'confidence': root.confidence,
                    'rationale': summary_text,
                    'evidence': evidence,
                    'sources': ['deepdebug'],
                },
                'hypotheses': [
                    {
                        'span_id': root_eid,
                        'step_index': root_step,
                        'agent_name': root_agent,
                        'confidence': root.confidence,
                        'rationale': summary_text,
                        'evidence': evidence,
                        'sources': ['deepdebug'],
                    }
                ],
            },
            audit=[
                DiagnosticAuditEntry(
                    stage=round_.name,
                    request_summary=round_.request_summary,
                    response_summary=round_.response_summary,
                    duration_ms=round_.duration_ms,
                    payload=round_.payload,
                )
                for round_ in rounds
            ],
            metadata={'analyzer': self.__class__.__name__, 'model': self.llm.model,
                      'backend': 'aao_moe', 'confidence': root.confidence,
                      'deepdebug_stages': [round_.name for round_ in rounds],
                      'evidence_verified': bool(evidence_references),
                      'rejected_evidence_count': rejected_evidence_count,
                      'prior_finding_count': len(self.prior_findings or []),
                      'memory_used': bool(reference_hint),
                      'memory_reference': (ref.description[:200] if ref else None)},
        )
        try:
            self.memory_store.save_run(trajectory, report)
        except Exception as exc:  # pragma: no cover
            LOG.warning('deep memory save failed: %s', exc)
        return DeepDebugResult(
            report=report,
            rounds=rounds,
            hypotheses=[],
            analysis=analysis,
            diagnosis=diagnosis,
        )

    # ---------------------------- memory + refine ---------------------------- #
    def _render_prior_findings(self, max_findings: int = 5) -> str:
        """Render upstream detector/judge findings as a fallible-hint block (G5).

        The block is explicit that these signals may be wrong, so the agent
        treats them as leads to check rather than answers to echo.
        """
        if not self.prior_findings:
            return ''
        lines: List[str] = []
        for f in list(self.prior_findings)[:max_findings]:
            mode = getattr(getattr(f, 'failure_mode', None), 'mode_id', None) or ''
            step = getattr(f, 'step_index', None)
            agent = getattr(f, 'agent_name', None) or ''
            evidence = '; '.join(list(getattr(f, 'evidence', []) or [])[:2])[:200]
            lines.append(
                f'- step={step} agent={agent} mode={mode}'
                + (f' evidence: {evidence}' if evidence else '')
            )
        if not lines:
            return ''
        return (
            'Prior signals from cheap detectors/judge (MAY BE WRONG --- verify '
            'against the trace before trusting; the manifested step is often '
            'downstream of the true cause):\n' + '\n'.join(lines)
        )

    def _retrieve_reference(self, trajectory: AgentTrajectory):
        """Top-1 historical reference (opt-in). Returns (ref, rendered_hint)."""
        if not self.use_memory:
            return None, ''
        try:
            refs = self.memory_store.search_references(trajectory, top_k=1)
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning('deep memory retrieval failed: %s', exc)
            return None, ''
        if not refs:
            return None, ''
        return refs[0], self._render_reference(refs[0])

    @staticmethod
    def _render_reference(ref: MemoryReference) -> str:
        """Compact reference block fed to the experts + the refine step."""
        parts = ['Historical reference case (a past similar failure -- guidance only):']
        if ref.description:
            parts.append(f'- Prior root cause: {ref.description[:300]}')
        if ref.evidence:
            parts.append(f'- Prior key evidence: {("; ".join(ref.evidence))[:300]}')
        if ref.suggestion:
            parts.append(f'- Prior fix pattern: {ref.suggestion[:300]}')
        return '\n'.join(parts)

    def _refine(
        self,
        trajectory: AgentTrajectory,
        root_event: Optional[AgentEvent],
        root_step: Optional[int],
        root_agent: Optional[str],
        reference_hint: str,
    ) -> Dict[str, Any]:
        """Single LLM call: localized step -> summary / evidence / suggestion."""
        if root_step is None:
            return {}
        evs = [e for e in trajectory.events if e.step_index is not None]
        lo, hi = root_step - 3, root_step + 3
        rows = [e for e in evs if lo <= e.step_index <= hi]
        root_event_id = getattr(root_event, 'event_id', None)
        doc = '\n'.join(
            f'Event {e.event_id} Step {e.step_index} [{e.agent_name}]'
            f'{" <<< ROOT" if e.event_id == root_event_id else ""}: '
            f'{(str(e.output) if e.output is not None else str(e.input or ""))[:600]}'
            f'{(" | ERROR: " + str(e.error)[:200]) if e.error else ""}'
            for e in rows) or f'Step {root_step}: (no content)'
        gt = str((trajectory.metadata or {}).get('ground_truth') or '').strip() \
            if self.use_ground_truth_context else ''
        gt_block = f'Correct answer: {gt}\n' if gt else ''
        ref_block = f'{reference_hint.strip()}\n\n' if reference_hint.strip() else ''
        user = (f'Goal: {trajectory.goal or "(unknown)"}\n{gt_block}{ref_block}'
                f'The localized ROOT-CAUSE is event {root_event_id}, '
                f'step {root_step} ({root_agent}).\n\n'
                f'Context:\n{doc}\n\n'
                f'Write the diagnosis for step {root_step}.')
        try:
            text = self.llm.complete(
                messages=[{'role': 'system', 'content': _REFINE_PROMPT},
                          {'role': 'user', 'content': user}],
                max_tokens=512).text or ''
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning('deep refine failed: %s', exc)
            return {}
        return extract_json_block(text) or {}

    @classmethod
    def _validate_evidence(
        cls,
        trajectory: AgentTrajectory,
        raw_evidence: List[Any],
    ) -> List[DeepDebugEvidence]:
        """Keep only quotes that occur in the cited trajectory event."""

        events = list(trajectory.events)
        by_id = {event.event_id: event for event in events}
        verified: List[DeepDebugEvidence] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_evidence:
            event = None
            quote = ''
            if isinstance(item, dict):
                event_id = str(item.get('event_id') or '').strip()
                quote = str(item.get('quote') or '').strip()
                event = by_id.get(event_id)
            else:
                quote = str(item).strip()
                matches = [
                    candidate for candidate in events
                    if cls._quote_matches_event(quote, candidate)
                ]
                event = matches[0] if len(matches) == 1 else None
            if event is None or not cls._quote_matches_event(quote, event):
                continue
            reference = (event.event_id, quote[:300])
            if reference in seen:
                continue
            seen.add(reference)
            verified.append(DeepDebugEvidence(*reference))
        return verified

    @staticmethod
    def _quote_matches_event(quote: str, event: AgentEvent) -> bool:
        normalized_quote = ' '.join(quote.split())
        if not normalized_quote:
            return False
        return any(
            normalized_quote in ' '.join(str(value).split())
            for value in (event.input, event.output, event.error)
            if value is not None
        )

    @staticmethod
    def _fallback_evidence(event: AgentEvent) -> Optional[DeepDebugEvidence]:
        for value in (event.error, event.output, event.input):
            text = ' '.join(str(value).split()) if value is not None else ''
            if text:
                return DeepDebugEvidence(event.event_id, text[:300])
        return None

    @staticmethod
    def _fallback_suggestion(event: AgentEvent) -> str:
        metadata = event.metadata or {}
        constraint = (
            metadata.get('dropped_constraint')
            or metadata.get('violated_constraint')
        )
        if constraint:
            return (
                f'Before executing this decision, verify the {constraint} '
                'constraint against the selected option and fail closed if it '
                'cannot be satisfied.'
            )
        return (
            'Re-evaluate this decision against every original task constraint '
            'and verify the corrected action before invoking any tool.'
        )


__all__ = [
    'DeepDebugAnalyzer',
    'DeepDebugDiagnosis',
    'DeepDebugEvidence',
    'DeepDebugResult',
    'DeepDebugRound',
]
