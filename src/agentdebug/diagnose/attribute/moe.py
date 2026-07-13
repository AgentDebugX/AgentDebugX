"""aao_moe — AllAtOnce + Mixture-of-Experts agreement attribution.

A single key-error-step localizer that beats BinarySearch on Who&When (both
Qwen models) and on AgentErrorBench-9B / Gemini-AEB, conceding only the
strong-model + no-ground-truth AEB-27B cell.

Design (one method, structure-routed Mixture-of-Experts + agreement arbitration):

  Expert A  = all_at_once  (whole-trace single shot; strong on long traces)
  Expert B  = moe, gated on a free structural signal:
                * multi-agent (Who&When)  -> cascade   (depth-adaptive root-seek:
                  which-half coarse narrowing to a <=6 window, then a holistic
                  root pick) -- corrects binary's LATE drift on multi-agent traces
                * single-agent (AgentErrorBench) -> bisect_refine (neutral
                  bisection + confidence-gated full-context endgame pick) --
                  converts binary's near->exact where its localization is noisy
  Arbitration (the "refine" step): if A and B pick the SAME step, that is the
  critical step (high agreement); if they DISAGREE, show only the two candidate
  steps' +-1 context windows and let the model pick between the two (short
  prompt). This fixes the long-trace (hand_crafted) collapse of a bare cascade.

Why MoE: an LLM which-half oracle has a fixed positional bias, but the gold
step's position is determined by the trace's structure (multi-agent gold is an
early decision; single-agent gold is a mid action-failure), and binary's error
DIRECTION flips between the two regimes. No single fixed oracle is optimal
everywhere, so we gate two oppositely-corrected experts and arbitrate.

Everything runs at temperature 0 (a non-voting localizer gains nothing from
sampling). Reuses :class:`AllAtOnceAttributor` / :class:`BinarySearchAttributor`.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import AgentEvent, AgentTrajectory, FailureFinding
from agentdebug.diagnose.attribute.attribution import (
    AllAtOnceAttributor,
    AttributionResult,
    BinarySearchAttributor,
    Blame,
)


# ----------------------------- small utils ----------------------------- #
def _gt(trajectory: AgentTrajectory) -> str:
    return str((trajectory.metadata or {}).get('ground_truth') or '').strip()


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _decision_events(trajectory: AgentTrajectory) -> List[AgentEvent]:
    """Agent decision steps (skip environment / pure-observation rows)."""
    out = [
        e for e in trajectory.events
        if e.step_index is not None
        and (e.agent_name or '').lower() != 'environment'
        and 'observation' not in str(getattr(e, 'event_type', '')).lower()
    ]
    return out or [e for e in trajectory.events if e.step_index is not None]


def _resolve_agent_name(trajectory: AgentTrajectory, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    names = [e.agent_name for e in trajectory.events if e.agent_name]
    if raw in names:
        return raw
    low = raw.lower()
    for n in names:
        if n and (n.lower() in low or low in n.lower()):
            return n
    return raw


def _is_multi_agent(trajectory: AgentTrajectory) -> bool:
    """True when a real multi-agent axis exists (Who&When) vs single-agent (AEB)."""
    names = {
        (e.agent_name or '').strip()
        for e in trajectory.events
        if e.agent_name and (e.agent_name or '').lower() not in ('environment', 'agent', '')
    }
    return len(names) >= 2


def _canon_base(name: Optional[str]) -> str:
    return re.split(r'\s*\(', str(name or ''))[0].strip().lower()


def _render_marked(evs: List[AgentEvent], mark_steps=None, max_chars: int = 420) -> str:
    ms = set(mark_steps) if mark_steps is not None else set()
    out = []
    for e in evs:
        mark = ' <<<' if e.step_index in ms else ''
        body = (str(e.output) if e.output is not None else str(e.input or ''))[:max_chars]
        out.append(f'Step {e.step_index} [{e.agent_name}]{mark}: {body}'
                   f'{(" | ERROR: " + str(e.error)[:160]) if e.error else ""}')
    return '\n'.join(out)


def _render_span(evs: List[AgentEvent], lo: int, hi: int,
                 max_chars: int = 320, max_rows: int = 14) -> str:
    rows = [e for e in evs if e.step_index is not None and lo <= e.step_index <= hi]
    if len(rows) > max_rows:
        h = max_rows // 2
        rows = rows[:h] + rows[-h:]
    out = []
    for e in rows:
        body = (str(e.output) if e.output is not None else str(e.input or ''))[:max_chars]
        out.append(f'Step {e.step_index} [{e.agent_name}]: {body}'
                   f'{(" | ERROR: " + str(e.error)[:140]) if e.error else ""}')
    return '\n'.join(out) or '(empty)'


# ------------------------------- prompts ------------------------------- #
_CASCADE_HALF = (
    'A run FAILED. A conversation segment is split into UPPER and LOWER halves. '
    'Which half contains the EARLIEST step whose OWN output is itself the decisive '
    'ROOT mistake (NOT where the error surfaces, NOT a tool/executor that merely '
    'reports a failing result, NOT a step that only inherits an earlier error)? '
    'If the upper (earlier) half already contains a genuinely wrong decision, '
    'pick upper.\n'
    'Respond ONLY with JSON: {"half": "upper" | "lower", "confidence": <float>}'
)
_ROOT_AAO = (
    'A multi-step agent run FAILED. Identify the DECISIVE ROOT-CAUSE: the '
    'EARLIEST step whose OWN output is itself wrong and makes the failure '
    'inevitable -- NOT a later step where the error becomes visible, NOT a '
    'tool/executor/terminal that merely runs or reports a failing result, and '
    'NOT a step that only inherits an earlier mistake. Also name the responsible '
    'component (agent).\n'
    'Respond ONLY with JSON: {"agent": "<component>", "step": <int>, '
    '"confidence": <float 0..1>}'
)
_NEUTRAL_HALF = (
    'A run FAILED. The segment is split into an UPPER (earlier) and a LOWER '
    '(later) half. Which half contains the DECISIVE error step -- the single step '
    'most responsible for the failure?\n'
    'Respond ONLY with JSON: {"half": "upper" | "lower", "confidence": <float>}'
)
_ENDGAME_PICK = (
    'A run FAILED. Using the WHOLE conversation as context, decide which ONE of '
    'the CANDIDATE steps is the DECISIVE error step: the step most responsible '
    'for the failure (the one whose correction would most likely have averted '
    'it). Also name the responsible component.\n'
    'Respond ONLY with JSON: {"step": <int from candidates>, "agent": '
    '"<component>", "confidence": <float 0..1>}'
)
_AAOMOE_TIE = (
    'A run FAILED. Two candidate error steps are shown with their context. Which '
    'ONE is the decisive critical error step (the one whose correction would most '
    'likely have averted the failure)?\n'
    'Respond ONLY with JSON: {"step": <int, one of the two>}'
)


def _ask(llm: LLMClient, system: str, user: str, max_tokens: int) -> str:
    try:
        return llm.complete(messages=[{'role': 'system', 'content': system},
                                      {'role': 'user', 'content': user}],
                            max_tokens=max_tokens).text or ''
    except Exception:
        return ''


# --------------------------- Expert B-1: cascade --------------------------- #
def _cascade(trajectory, *, llm, label_hint, candidate_labels,
             rounds=8, window_min=6, use_gt=True, max_tokens=512, reference_hint='') -> Dict[str, Any]:
    """Depth-adaptive root-seeking cascade (multi-agent expert)."""
    evs = _decision_events(trajectory)
    steps = sorted({e.step_index for e in evs if e.step_index is not None})
    if not steps:
        return {'agent_name': None, 'step_index': None, 'confidence': 0.0}
    gt = _gt(trajectory) if use_gt else ''
    gt_block = f'The correct answer to the task is: {gt}\n' if gt else ''
    ref_block = f'{reference_hint.strip()}\n' if reference_hint.strip() else ''
    goal = trajectory.goal or '(unknown)'

    lo, hi = 0, len(steps) - 1
    for _ in range(rounds):
        if hi - lo + 1 <= window_min:
            break
        mid = (lo + hi) // 2
        user = (f'Goal: {goal}\n{gt_block}'
                f'UPPER (steps {steps[lo]}..{steps[mid]}):\n{_render_span(evs, steps[lo], steps[mid])}\n\n'
                f'LOWER (steps {steps[mid+1]}..{steps[hi]}):\n{_render_span(evs, steps[mid+1], steps[hi])}\n\n'
                f'Which half contains the earliest ROOT mistake?')
        half = str((extract_json_block(_ask(llm, _CASCADE_HALF, user, 128)) or {}).get('half') or '').strip().lower()
        if half == 'upper':
            hi = mid
        elif half == 'lower':
            lo = mid + 1
        else:
            break
    window = steps[lo:hi + 1]

    if len(window) == 1:
        root = window[0]
    else:
        win = set(window)
        cand = f'Valid component values: {candidate_labels}\n' if candidate_labels else ''
        doc = '\n'.join(
            f'Step {e.step_index} [{e.agent_name}]: '
            f'{(str(e.output) if e.output is not None else str(e.input or ""))[:900]}'
            f'{(" | ERROR: " + str(e.error)[:160]) if e.error else ""}'
            for e in evs if e.step_index in win)
        user = (f'Goal: {goal}\n{gt_block}{ref_block}{label_hint}\n{cand}'
                f'Candidate steps: {window}\n\n{doc}\n\n'
                f'Which step here is the decisive cause, and which component?')
        st = _as_int((extract_json_block(_ask(llm, _ROOT_AAO, user, max_tokens)) or {}).get('step'))
        root = st if st in win else window[0]

    ag = next((e.agent_name for e in evs if e.step_index == root and e.agent_name), None)
    return {'agent_name': str(ag).split(' (')[0].strip() if ag else None,
            'step_index': root, 'confidence': 0.6}


# ------------------------ Expert B-2: bisect_refine ------------------------ #
def _bisect_refine(trajectory, *, llm, label_hint, candidate_labels,
                   endgame=3, conf_floor=0.75, use_gt=True, max_tokens=512, reference_hint='') -> Dict[str, Any]:
    """Neutral bisection + confidence-gated full-context endgame (single-agent expert)."""
    evs = _decision_events(trajectory)
    steps = sorted({e.step_index for e in evs if e.step_index is not None})
    if not steps:
        return {'agent_name': None, 'step_index': None, 'confidence': 0.0}
    gt = _gt(trajectory) if use_gt else ''
    gt_block = f'The correct answer to the task is: {gt}\n' if gt else ''
    ref_block = f'{reference_hint.strip()}\n' if reference_hint.strip() else ''
    goal = trajectory.goal or '(unknown)'

    def half_probe(lo, hi):
        mid = (lo + hi) // 2
        user = (f'Goal: {goal}\n{gt_block}'
                f'UPPER (steps {steps[lo]}..{steps[mid]}):\n{_render_span(evs, steps[lo], steps[mid])}\n\n'
                f'LOWER (steps {steps[mid+1]}..{steps[hi]}):\n{_render_span(evs, steps[mid+1], steps[hi])}\n\n'
                f'Which half contains the decisive error step?')
        p = extract_json_block(_ask(llm, _NEUTRAL_HALF, user, 96)) or {}
        return str(p.get('half') or '').strip().lower(), _as_float(p.get('confidence'), 0.5), mid

    lo, hi = 0, len(steps) - 1
    endgame_conf = 1.0
    while hi - lo + 1 > endgame:
        half, conf, mid = half_probe(lo, hi)
        if half == 'upper':
            hi = mid
        elif half == 'lower':
            lo = mid + 1
        else:
            break
        endgame_conf = conf
    window = steps[lo:hi + 1]

    if len(window) == 1:
        root = window[0]
    elif endgame_conf >= conf_floor:
        while hi - lo + 1 > 1:
            half, _c, mid = half_probe(lo, hi)
            if half == 'upper':
                hi = mid
            elif half == 'lower':
                lo = mid + 1
            else:
                break
        root = steps[lo]
    else:
        cand = f'Valid component values: {candidate_labels}\n' if candidate_labels else ''
        doc = _render_marked(evs, mark_steps=set(window))
        user = (f'Goal: {goal}\n{gt_block}{ref_block}{label_hint}\n{cand}'
                f'CANDIDATE steps (pick exactly one): {window}\n\n{doc}\n\n'
                f'Which candidate is the decisive error step?')
        st = _as_int((extract_json_block(_ask(llm, _ENDGAME_PICK, user, max_tokens)) or {}).get('step'))
        root = st if st in set(window) else window[len(window) // 2]

    ag = next((e.agent_name for e in evs if e.step_index == root and e.agent_name), None)
    return {'agent_name': str(ag).split(' (')[0].strip() if ag else None,
            'step_index': root, 'confidence': 0.6}


# ------------------------------ MoE gate ------------------------------ #
def _moe(trajectory, *, llm, label_hint, candidate_labels, short_max=12, use_gt=True,
         max_tokens=512, reference_hint=''):
    if _is_multi_agent(trajectory):
        return _cascade(trajectory, llm=llm, label_hint=label_hint,
                        candidate_labels=candidate_labels, use_gt=use_gt,
                        max_tokens=max_tokens, reference_hint=reference_hint)
    return _bisect_refine(trajectory, llm=llm, label_hint=label_hint,
                          candidate_labels=candidate_labels, use_gt=use_gt,
                          max_tokens=max_tokens, reference_hint=reference_hint)


# ---------------------- aao_moe: experts + arbitration ---------------------- #
def aao_moe_attribute(
    trajectory: AgentTrajectory, *, llm: LLMClient,
    label_hint: str = '', candidate_labels: Optional[List[str]] = None,
    ctx_before: int = 1, ctx_after: int = 1,
    use_ground_truth_context: bool = True, max_tokens: int = 256,
    reference_hint: str = '',
) -> Dict[str, Any]:
    """Run the two experts and arbitrate. Returns {agent_name, step_index,
    confidence, raw} where raw carries the audit (expert picks, agreement).

    ``reference_hint`` is an optional retrieved historical reference case fed to
    BOTH experts (empty -> no change)."""
    evs = _decision_events(trajectory)
    # Expert A: all_at_once
    try:
        ar = AllAtOnceAttributor(
            llm=llm, use_ground_truth_context=use_ground_truth_context,
            extra_context=reference_hint).attribute(trajectory)
        ah = ar.hypotheses[0] if ar.hypotheses else None
        a_step, a_ag = (ah.step_index, ah.agent_name) if ah else (None, None)
    except Exception:
        a_step, a_ag = None, None
    # Expert B: moe
    m = _moe(trajectory, llm=llm, label_hint=label_hint, candidate_labels=candidate_labels,
             use_gt=use_ground_truth_context, max_tokens=512, reference_hint=reference_hint)
    m_step, m_ag = m.get('step_index'), m.get('agent_name')

    audit = {'expert_aao': {'step': a_step, 'agent': a_ag},
             'expert_moe': {'step': m_step, 'agent': m_ag}}

    # agreement -> critical step
    if a_step is not None and a_step == m_step:
        return {'agent_name': m_ag or a_ag, 'step_index': m_step, 'confidence': 0.8,
                'raw': {**audit, 'verdict': 'agreement'}}
    if a_step is None:
        return {**m, 'raw': {**audit, 'verdict': 'aao_empty->moe'}}
    if m_step is None:
        return {'agent_name': a_ag, 'step_index': a_step, 'confidence': 0.6,
                'raw': {**audit, 'verdict': 'moe_empty->aao'}}

    # disagree -> +-ctx window of each candidate, pick one (short prompt)
    def _ctx(s):
        rows = [e for e in evs if e.step_index is not None and s - ctx_before <= e.step_index <= s + ctx_after]
        return '\n'.join(
            f'Step {e.step_index} [{e.agent_name}]: '
            f'{(str(e.output) if e.output is not None else str(e.input or ""))[:400]}'
            f'{(" | ERROR: " + str(e.error)[:120]) if e.error else ""}'
            for e in rows) or f'Step {s}: (no content)'
    gt = _gt(trajectory) if use_ground_truth_context else ''
    gt_block = f'Correct answer: {gt}\n' if gt else ''
    user = (f'Goal: {trajectory.goal or "(unknown)"}\n{gt_block}'
            f'CANDIDATE A = step {a_step}:\n{_ctx(a_step)}\n\n'
            f'CANDIDATE B = step {m_step}:\n{_ctx(m_step)}\n\n'
            f'Which is the critical error step, {a_step} or {m_step}?')
    st = _as_int((extract_json_block(_ask(llm, _AAOMOE_TIE, user, max_tokens)) or {}).get('step'))
    if st == a_step:
        return {'agent_name': a_ag, 'step_index': a_step, 'confidence': 0.65,
                'raw': {**audit, 'verdict': 'arbitrate->aao'}}
    return {'agent_name': m_ag, 'step_index': m_step, 'confidence': 0.65,
            'raw': {**audit, 'verdict': 'arbitrate->moe'}}


class AaoMoeAttributor:
    """AllAtOnce + MoE agreement attributor (the v0.3 default localizer).

    Two experts (all_at_once + structure-gated cascade/bisect_refine) with a
    +-1-window agreement arbitration as the refine step. Beats BinarySearch on
    Who&When (both Qwen models) and AEB-9B / Gemini-AEB; ties/concedes AEB-27B.
    """

    id = 'aao_moe'

    def __init__(
        self,
        llm: LLMClient,
        *,
        label_hint: str = '',
        candidate_labels: Optional[List[str]] = None,
        ctx_before: int = 1,
        ctx_after: int = 1,
        use_ground_truth_context: bool = False,
        reference_hint: str = '',
    ) -> None:
        self.llm = llm
        self.label_hint = label_hint
        self.candidate_labels = candidate_labels
        self.ctx_before = ctx_before
        self.ctx_after = ctx_after
        self.use_ground_truth_context = use_ground_truth_context
        self.reference_hint = reference_hint

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        started = time.perf_counter()
        out = aao_moe_attribute(
            trajectory, llm=self.llm, label_hint=self.label_hint,
            candidate_labels=self.candidate_labels, ctx_before=self.ctx_before,
            ctx_after=self.ctx_after, use_ground_truth_context=self.use_ground_truth_context,
            reference_hint=self.reference_hint)
        root = out.get('step_index')
        span_id = next((e.event_id for e in trajectory.events
                        if e.step_index == root and getattr(e, 'event_id', None)), None)
        blame = Blame(
            span_id=span_id,
            step_index=root,
            agent_name=out.get('agent_name'),
            confidence=out.get('confidence', 0.6),
            rationale='AllAtOnce + MoE agreement: '
                      + str(out.get('raw', {}).get('verdict', '')),
            sources=['aao_moe'],
        )
        return AttributionResult(
            method=self.id,
            hypotheses=[blame],
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            raw=out.get('raw', {}),
        )


__all__ = ['AaoMoeAttributor', 'aao_moe_attribute']
