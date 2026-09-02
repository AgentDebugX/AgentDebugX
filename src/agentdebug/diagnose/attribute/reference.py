"""Attribution conditioned on a SUCCESSFUL reference run of the same task.

Contributed from AgentErrorData, where this was the `with_reference_success` debug method:
the same all-at-once attribution, with one addition -- a successful trajectory for the same
task is placed in the prompt, and the first point where the failed run's actions diverge from
it is named as a candidate. Upstreamed so it is one implementation rather than two.

The reference is deliberately a separately-labelled attributor (`id='with_reference_success'`)
rather than a default. Two reasons, both measured on the AED corpus: the value of the reference
has to be quantifiable, which needs rows without it; and there is a failure mode where the
model learns to copy the reference instead of reasoning, which needs rows with it to be
distinguishable.

The block is injected through `AllAtOnceAttributor.extra_context`, so nothing in the base
attributor changes. A reference is one valid path, not the only one: the prompt says so, and
`diff_traces` reports the first divergence as a *candidate*, not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from agentdebug.schema.models import AgentTrajectory, EventType

from .attribution import AllAtOnceAttributor, Attributor, LLMClient


@dataclass
class Action:
    step: int
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)

    def key(self, args_sensitive: bool = True) -> str:
        if not args_sensitive:
            return self.tool
        return f'{self.tool}:{sorted((str(k), str(v)) for k, v in self.args.items())}'


@dataclass
class TraceDiffResult:
    first_divergence_step: Optional[int]
    failed_action: Optional[Action]
    reference_action: Optional[Action]
    aligned_prefix: int
    candidate_steps: List[int] = field(default_factory=list)


def extract_actions(trajectory: AgentTrajectory) -> List[Action]:
    """Tool calls in order. `input` is `{'tool': ..., 'args': {...}}`, the shape
    `_normalize_action` reads and `CorrectedAction` is built from."""
    out: List[Action] = []
    for e in trajectory.events:
        if e.event_type == EventType.TOOL_CALL and isinstance(e.input, dict) and e.input.get('tool'):
            step = e.step_index if e.step_index is not None else len(out)
            out.append(Action(step=step, tool=str(e.input['tool']), args=e.input.get('args') or {}))
    return out


def diff_traces(
    failed: AgentTrajectory,
    reference: AgentTrajectory,
    *,
    args_sensitive: bool = True,
    n_candidates: int = 3,
) -> TraceDiffResult:
    """Align two action sequences and report the first divergence.

    Prefix alignment, not edit distance: the question is "where did this run first leave the
    path the successful run took", and a prefix walk answers exactly that. The steps around the
    divergence are returned as candidates because a wrong action is often the consequence of
    the one before it.
    """
    fa, ra = extract_actions(failed), extract_actions(reference)
    n = 0
    for a, b in zip(fa, ra):
        if a.key(args_sensitive) != b.key(args_sensitive):
            break
        n += 1
    if n == len(fa) and n == len(ra):
        return TraceDiffResult(None, None, None, n)
    f_act = fa[n] if n < len(fa) else None
    r_act = ra[n] if n < len(ra) else None
    div = f_act.step if f_act else (fa[-1].step if fa else None)
    cands: List[int] = []
    if div is not None:
        for s in range(div, div + n_candidates):
            cands.append(s)
        if div - 1 >= 0 and div - 1 not in cands:
            cands.insert(0, div - 1)
    return TraceDiffResult(div, f_act, r_act, n, cands[:n_candidates])


def pick_reference(
    failed: AgentTrajectory,
    candidates: Sequence[AgentTrajectory],
    *,
    args_sensitive: bool = True,
) -> Optional[AgentTrajectory]:
    """The successful candidate sharing the LONGEST action prefix with the failed run.

    Same task, several successful runs: the one that walked furthest with the failed run
    before diverging is the one whose divergence point is most informative.
    """
    best, best_n = None, -1
    for c in candidates:
        n = diff_traces(failed, c, args_sensitive=args_sensitive).aligned_prefix
        if n > best_n:
            best, best_n = c, n
    return best


def render_reference(trajectory: AgentTrajectory, *, max_field_chars: int = 300) -> str:
    """One line per event, in the SAME shape the all-at-once prompt renders the failed run,
    so the two read alike and the model is not cued by formatting."""

    def clip(v: Any) -> str:
        s = str(v) if v is not None else ''
        return s if len(s) <= max_field_chars else s[:max_field_chars] + '…'

    return '\n'.join(
        f'Step {e.step_index if e.step_index is not None else "?"} [{e.event_id}] {e.agent_name}: '
        f'{clip(e.output) if e.output is not None else clip(e.input)}'
        f'{(" | ERROR: " + clip(e.error)) if e.error else ""}'
        for e in trajectory.events
    ) or '(empty conversation)'


def build_reference_block(
    reference: Optional[AgentTrajectory],
    *,
    failed: Optional[AgentTrajectory] = None,
    prior_text: Optional[str] = None,
    max_field_chars: int = 300,
) -> str:
    """The text placed in the prompt. Empty when there is nothing to say."""
    if reference is None and not prior_text:
        return ''
    parts = ['# Reference: a SUCCESSFUL run of this same task']
    if reference is not None:
        parts.append(render_reference(reference, max_field_chars=max_field_chars))
        if failed is not None:
            d = diff_traces(failed, reference)
            if d.first_divergence_step is not None:
                parts.append(
                    f'\nThe failed run first diverges from this reference at step '
                    f'{d.first_divergence_step}'
                    + (f' (it called {d.failed_action.tool}' if d.failed_action else '')
                    + (f' where the reference called {d.reference_action.tool})'
                       if d.reference_action else ')' if d.failed_action else '')
                    + '. Treat that as a candidate, not a conclusion.'
                )
    if prior_text:
        parts.append(f'\nAlignment prior: {prior_text}')
    parts.append(
        '\nThe reference is one valid path, not the only one. Do NOT assume every deviation '
        'from it is an error -- judge against the goal, not against the reference.'
    )
    return '\n'.join(parts)


class ReferenceAttributor(AllAtOnceAttributor):
    """All-at-once attribution with a successful reference run in context.

    Everything else -- retry, fallback, corrected-action extraction, `Blame.sources` stamping --
    is inherited unchanged. Only `id` and the injected context differ, which is what lets rows
    produced with and without a reference be told apart afterwards.
    """

    id = 'with_reference_success'

    def __init__(
        self,
        llm: LLMClient,
        reference: Optional[AgentTrajectory],
        *,
        failed: Optional[AgentTrajectory] = None,
        prior_text: Optional[str] = None,
        max_field_chars: int = 300,
        fallback: Optional[Attributor] = None,
        **kwargs: Any,
    ) -> None:
        block = build_reference_block(
            reference, failed=failed, prior_text=prior_text, max_field_chars=max_field_chars)
        extra = kwargs.pop('extra_context', '')
        combined = f'{block}\n\n{extra}'.strip() if extra else block
        super().__init__(llm, fallback=fallback, extra_context=combined, **kwargs)
        self.reference = reference
