"""DeepDebug — AllAtOnce + Mixture-of-Experts key-error-step attribution.

DeepDebug localizes the decisive failure step with the ``aao_moe`` method
(:mod:`agentdebug.diagnose.actions.moe`):

  * Two experts produce candidates — ``all_at_once`` (whole-trace single shot)
    and a structure-gated MoE (multi-agent -> root-seeking cascade; single-agent
    -> neutral bisection + full-context endgame).
  * The *refine* step is an agreement arbitration: if the two experts pick the
    SAME step, that is the root cause; if they disagree, the model picks between
    the two candidates shown with their +-1 context windows (short prompt).

This beats BinarySearch on Who&When (both Qwen models) and on AgentErrorBench-9B
/ Gemini-AEB, conceding only the strong-model + no-ground-truth AEB-27B cell.
Runs at temperature 0; it never re-executes the agent.

Each run records a single :class:`DeepDebugRound` (the expert picks + the
arbitration verdict) so the diagnosis stays auditable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentdebug.core.llm import LLMClient, extract_json_block
from agentdebug.core.models import (
    AgentTrajectory,
    DiagnosticReport,
    FailureFinding,
    FailureMode,
)
from agentdebug.diagnose.deep_memory import (
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
    'Respond ONLY with JSON: {"summary": "<1-2 sentences>", '
    '"evidence": ["<short quoted snippet>", ...], "suggestion": "<one fix>"}'
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


class DeepDebugAnalyzer:
    """Key-error-step attributor backed by the ``aao_moe`` localizer.

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

        Pipeline: (0) optional top-1 historical retrieval -> (1) aao_moe
        localization (two experts + arbitration, fed the reference) -> (2)
        refine: a single LLM call that turns the localized step into a
        ``summary`` / ``evidence`` / ``suggestion`` (the step is fixed, not
        re-decided). Two DeepDebugRounds record the audit trail.
        """
        from agentdebug.diagnose.actions.moe import aao_moe_attribute

        started = time.perf_counter()
        # Step 0: optional top-1 historical retrieval (opt-in via use_memory).
        ref, reference_hint = self._retrieve_reference(trajectory)
        findings_hint = self._render_prior_findings()
        if findings_hint:
            reference_hint = (
                f'{reference_hint}\n\n{findings_hint}' if reference_hint
                else findings_hint
            )

        # Step 1: localize the decisive step (two experts + arbitration).
        out = aao_moe_attribute(
            trajectory, llm=self.llm, label_hint=self.label_hint,
            candidate_labels=self.candidate_labels, ctx_before=self.ctx_before,
            ctx_after=self.ctx_after, use_ground_truth_context=self.use_ground_truth_context,
            reference_hint=reference_hint)
        raw = out.get('raw', {})
        root_step = out.get('step_index')
        root_agent = out.get('agent_name')
        root_ev = next((e for e in trajectory.events if e.step_index == root_step), None)
        root_eid = getattr(root_ev, 'event_id', None) if root_ev else None
        aao_ms = int((time.perf_counter() - started) * 1000)

        # Step 2: refine -> human-readable summary / evidence / suggestion for
        # the localized step (the located step itself is fixed, not re-decided).
        refine_started = time.perf_counter()
        refined = self._refine(trajectory, root_step, root_agent, reference_hint)
        summary_text = str(refined.get('summary') or '').strip()
        evidence = [str(e)[:300] for e in (refined.get('evidence') or []) if str(e).strip()]
        suggestion = str(refined.get('suggestion') or '').strip()
        if not evidence:
            ev_err = getattr(root_ev, 'error', None) if root_ev else None
            evidence = [str(ev_err)[:200]] if ev_err else []

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
                confidence=out.get('confidence', 0.6),
                evidence=evidence,
                suggestion=suggestion or None,
            )]

        summary = summary_text or (
            f'AllAtOnce+MoE agreement ({raw.get("verdict", "")}): '
            f'root cause at step {root_step} ({root_agent}).')

        rounds = [
            DeepDebugRound(
                name='aao_moe',
                request_summary=f'experts: aao={raw.get("expert_aao")} moe={raw.get("expert_moe")}',
                response_summary=f'verdict={raw.get("verdict")} -> step={root_step} agent={root_agent}',
                duration_ms=aao_ms,
                payload=raw,
            ),
            DeepDebugRound(
                name='refine',
                request_summary=f'refine step {root_step} ({root_agent})'
                                f'{" + memory" if reference_hint else ""}',
                response_summary=(summary_text or '(template summary)')[:200],
                duration_ms=int((time.perf_counter() - refine_started) * 1000),
                payload=refined,
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
            metadata={'analyzer': self.__class__.__name__, 'model': self.llm.model,
                      'backend': 'aao_moe', 'confidence': out.get('confidence'),
                      'memory_used': bool(reference_hint),
                      'memory_reference': (ref.description[:200] if ref else None)},
        )
        try:
            self.memory_store.save_run(trajectory, report)
        except Exception as exc:  # pragma: no cover
            LOG.warning('deep memory save failed: %s', exc)
        return DeepDebugResult(report=report, rounds=rounds, hypotheses=[])

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
        doc = '\n'.join(
            f'Step {e.step_index} [{e.agent_name}]'
            f'{" <<< ROOT" if e.step_index == root_step else ""}: '
            f'{(str(e.output) if e.output is not None else str(e.input or ""))[:600]}'
            f'{(" | ERROR: " + str(e.error)[:200]) if e.error else ""}'
            for e in rows) or f'Step {root_step}: (no content)'
        gt = str((trajectory.metadata or {}).get('ground_truth') or '').strip() \
            if self.use_ground_truth_context else ''
        gt_block = f'Correct answer: {gt}\n' if gt else ''
        ref_block = f'{reference_hint.strip()}\n\n' if reference_hint.strip() else ''
        user = (f'Goal: {trajectory.goal or "(unknown)"}\n{gt_block}{ref_block}'
                f'The localized ROOT-CAUSE is step {root_step} ({root_agent}).\n\n'
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


__all__ = ['DeepDebugAnalyzer', 'DeepDebugResult', 'DeepDebugRound']
