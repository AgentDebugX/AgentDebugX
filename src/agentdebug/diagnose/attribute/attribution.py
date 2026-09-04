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

A ``Blame`` may also carry a :class:`CorrectedAction` — the concrete action that should
have replaced the blamed one, in the trace's own ``{"tool", "args"}`` shape. It is opt-in
per attributor (``propose_corrected_action=True``), always nullable, and never guessed;
``AttributionResult.raw['corrected_action']`` says why one is absent. A rationale explains
the past, so a harness that re-runs a trajectory with exactly one step substituted cannot
use it; this is the field that makes that rerun possible.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, cast


# Forward decl so BinarySearchAttributor.attribute can reference _EllipsisEvent
# from the helper render path; defined later in the module.

from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.runtime.llm import TokenUsage
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    FailureFinding,
    confidence_or_default,
)

LOG = logging.getLogger('agentdebug.attribution')


@dataclass
class CorrectedAction:
    """A concrete replacement action for the blamed step, in the trace's own action shape.

    A `rationale` explains the past; this is the thing a harness can actually execute.
    Downstream consumers that re-run a trajectory with exactly one step substituted need a
    machine-readable action, not prose about one -- and until this existed, every attributor
    in this module returned prose only, so a substitution rerun was unreachable through the
    library however good the localization was.

    Shape: `tool` + `args`, which is what a `tool.call` event carries in its `input`. Use
    :meth:`as_event_input` to get exactly that dict back.

    HONESTY CONTRACT -- three states, all distinguishable:

    * `Blame.corrected_action is None` -- the attributor did not produce one. Either it
      cannot (Heuristic and SBFL have no model to ask), it was not asked
      (`propose_corrected_action=False`, the default), or it was asked and declined. Read
      `AttributionResult.raw['corrected_action']` for which of those it was.
    * `differs_from_original is True` -- a real substitution: replaying this action changes
      what the agent did.
    * `differs_from_original is False` -- the attributor named an action identical to what
      the trace already did at that step. Substituting it is a no-op, so a rerun cannot
      attribute anything to the substitution. This must NOT be mistaken for a fix.
    * `differs_from_original is None` -- the blamed step has no readable action of its own
      (a plan, a reflection, an observation), so "different" is not a question that has an
      answer here. Not the same as False.
    """

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    #: Attributor id that produced it, so an ensemble's action stays traceable to a backend.
    source: str = ''
    #: The blamed step's own action as read from the trace, or None if it had none.
    original: Optional[Dict[str, Any]] = None
    #: True/False/None per the honesty contract above. Never guessed.
    differs_from_original: Optional[bool] = None

    def as_event_input(self) -> Dict[str, Any]:
        """`{'tool': ..., 'args': {...}}` -- the shape a `tool.call` event's `input` uses."""
        return {'tool': self.tool, 'args': dict(self.args)}


@dataclass
class Blame:
    span_id: Optional[str]
    step_index: Optional[int]
    agent_name: Optional[str]
    confidence: float
    rationale: str
    evidence: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    #: Concrete replacement action for this step, when the attributor could produce one.
    #: Defaulted to None, so every existing construction site and every third-party
    #: Attributor keeps working unchanged, and a caller that ignores the field sees no
    #: change at all. See :class:`CorrectedAction` for what None does and does not mean.
    corrected_action: Optional['CorrectedAction'] = None

    # -- Error state ------------------------------------------------------
    #
    # Blame previously answered only "which step", which forces the ranking to
    # treat every candidate as equally live. It is not: an agent that errs at
    # step 3, notices at step 39 and corrects should not have step 3 blamed for
    # a failure at step 60. Nothing in this dataclass could express that, so
    # `HeuristicAttributor` -- which sorts by step index and takes the first --
    # blames the earliest candidate whether or not the agent recovered from it.
    #
    # These are Optional and default to None, meaning "this attributor does not
    # model state", which is true of every attributor that predates them.
    #: e.g. ``"fixed_at_step_39"``; None when unfixed or unassessed.
    fix_status: Optional[str] = None
    #: Verbatim text showing the agent correcting course. Without it,
    #: ``fix_status`` is an assertion rather than a claim that can be checked.
    fix_evidence_quote: Optional[str] = None
    #: Does this error actually reach the terminal failure? False means the run
    #: failed for some other reason and this candidate is a distraction.
    chain_membership: Optional[bool] = None
    #: How it reached the ending -- e.g. ``"budget_debt"`` for an error that
    #: never broke correctness but consumed the step or token budget.
    terminal_connection: Optional[str] = None
    #: Step indices this error caused to produce no progress.
    wasted_steps: List[int] = field(default_factory=list)


@dataclass
class AttributionResult:
    method: str
    hypotheses: List[Blame]
    elapsed_ms: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)
    #: Tokens and cost this attribution consumed. Defaulted, so every existing construction
    #: site and every third-party Attributor keeps working unchanged; LLM-backed attributors
    #: fill it in. Without it, comparing a cheap attributor against an expensive one on the
    #: same trace is impossible — which is the comparison anyone choosing between
    #: all_at_once, binary_search and ensemble actually needs to make.
    usage: TokenUsage = field(default_factory=TokenUsage)


class Attributor(Protocol):
    id: str

    #: Whether this attributor needs detector findings to produce anything at all.
    #:
    #: Read it with `getattr(attributor, 'requires_findings', False)`: it is documented on the
    #: protocol so callers can branch on it, but every existing third-party Attributor that
    #: predates this field keeps satisfying the protocol without declaring it, and False is
    #: the correct default for an attributor that reads the trajectory directly.
    requires_findings: bool

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        ...


class AttributionUnavailable(RuntimeError):
    """The model-based attributor produced nothing and the caller refused a substitute.

    Raised by :data:`NO_FALLBACK` where an attributor would otherwise hand the
    trajectory to its ``fallback``. The message names the attributor and the
    reason (an LLM error, or a reply with no JSON block), so a corpus can
    record the attempt as a dropout instead of a heuristic label that looks
    like a model one.
    """


class NoFallback:
    """An ``Attributor`` that refuses, for callers who want fail-closed attribution.

    Every model-based attributor defaults to ``fallback or HeuristicAttributor()``:
    when the model call fails or returns no JSON, a model-free ranking of the
    detector findings is returned under the *same* result type, and nothing
    downstream can tell the two apart. Pass ``fallback=NO_FALLBACK`` to make that
    path raise :class:`AttributionUnavailable` instead. Measured by a consumer
    on 887 rows: the silent substitute fired on 16.7% of one model's attempts
    against 0.5% of another's, all labelled as model attributions.
    """

    id = 'no_fallback'
    requires_findings = False

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        raise AttributionUnavailable(
            f'attribution unavailable for trace {trajectory.trace_id!r}: the model-based '
            'attributor produced no usable result and fallback is disabled (NO_FALLBACK)'
        )


#: Pass as ``fallback=`` to any attributor to disable the heuristic substitute.
NO_FALLBACK = NoFallback()


class HeuristicAttributor:
    """Cheap, model-free fallback that picks the earliest highest-confidence finding.

    Requires detector findings. It ranks findings; it does not derive them, so calling
    `attribute(trajectory)` with no `findings` can only ever return zero hypotheses. Run it
    through `DiagnosePipeline` (which detects first and passes findings in), or pass findings
    yourself. See `requires_findings`.
    """

    id = 'heuristic'

    #: This attributor cannot produce hypotheses from a trajectory alone.
    #:
    #: Exposed so callers can tell "no findings were supplied" apart from "the trajectory
    #: contains no attributable failure" BEFORE spending anything. The motivating case: a
    #: downstream harness used the bare attributor as a fallback for when `DiagnosePipeline`
    #: raised, and measured `heuristic` as returning no hypotheses on 5 of 5 trajectories --
    #: not because the trajectories were clean, but because a findings-less call is empty by
    #: construction. Under the pipeline the same attributor produced hypotheses on 3 of those
    #: 5. A fallback to the bare attributor is therefore not a safety net for this attributor;
    #: it is a guaranteed empty result that looks like a verdict.
    #:
    #: Other attributors in this module leave it False: they read the trajectory directly.
    requires_findings: bool = True

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        findings = findings or []
        if not findings:
            # Empty, as before -- but say why, so an empty result is self-describing rather
            # than indistinguishable from "nothing to blame here". `raw` is already a
            # defaulted dict on AttributionResult, so nothing about the shape changes.
            return AttributionResult(
                method=self.id,
                hypotheses=[],
                raw={
                    'reason': 'no_findings_supplied',
                    'detail': (
                        'HeuristicAttributor ranks detector findings and cannot derive them. '
                        'Call it through DiagnosePipeline, or pass findings= explicitly.'
                    ),
                    'requires_findings': True,
                },
            )
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
                    # Model-free: this attributor ranks findings a detector already wrote. It
                    # has nothing to ask what should have been done instead, so it never
                    # guesses one. See raw['corrected_action'] below.
                    corrected_action=None,
                )
            ],
            raw={
                'corrected_action': _corrected_action_report(
                    requested=False, action=None, reason='no_llm', source=self.id,
                ),
            },
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


#: Appended to an attributor's own JSON schema when the caller opted in. The field is
#: nullable ON PURPOSE: a model that cannot name a concrete action must say so, because a
#: plausible-looking guess is indistinguishable downstream from a real correction and will
#: be replayed as if it were one.
_CORRECTED_ACTION_RULES = """
"corrected_action" is the ONE concrete action that should have replaced the blamed step's
action, as {"tool": "<tool name>", "args": {<arguments>}}.

Rules for it:
1. Use the tool names and argument keys the trajectory itself uses. Do not invent a tool the
   agent had no access to.
2. "args" must be a JSON object.
3. Return null unless you can name a specific tool and its arguments. A placeholder, a
   paraphrase of your rationale, or a guess is WORSE than null.
4. Return null when the blamed step is not an action at all (a plan, a reflection, an
   observation), and when the step's own action was already correct.
"""


#: Used verbatim in place of `_ATTR_SYSTEM_PROMPT` when `propose_corrected_action=True`.
#: Kept as a separate constant rather than an f-string patch so that with the flag off the
#: prompt an existing caller gets is byte-identical to what it got before this feature.
_ATTR_SYSTEM_PROMPT_WITH_ACTION = """You are an AI assistant tasked with analyzing agent conversation history when solving a real world problem.

Respond ONLY with a JSON object matching this schema (no prose, no markdown):

{
  "span_id": "<event_id from the input or null>",
  "step_index": <int or null>,
  "agent_name": "<agent_name from the input or null>",
  "confidence": <float between 0 and 1>,
  "rationale": "<one or two sentences justifying the choice>",
  "evidence": ["<short quoted evidence>", ...],
  "corrected_action": {"tool": "<tool name>", "args": {<arguments>}} or null
}

If the trajectory does not appear to have failed, return all fields as null and
confidence 0.
""" + _CORRECTED_ACTION_RULES


class AllAtOnceAttributor:
    """LLM-based attributor mirroring Who&When's All-at-Once method."""

    id = 'all_at_once'

    def __init__(
        self,
        llm: LLMClient,
        *,
        fallback: Optional[Attributor] = None,
        max_findings: int = 20,
        # Raised from 4096: thinking models (Gemini, o-series) can spend the
        # entire budget on hidden reasoning tokens before emitting any
        # visible JSON, returning an empty completion. Measured against a
        # real Terminal-Bench trajectory: gemini-3.1-pro used
        # reasoning_tokens=7866 on this exact call and got truncated at 4096.
        max_tokens: int = 16000,
        use_ground_truth_context: bool = False,
        save_full_generation: bool = False,
        extra_context: str = '',
        propose_corrected_action: bool = False,
    ) -> None:
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_findings = max_findings
        self.max_tokens = max_tokens
        self.use_ground_truth_context = use_ground_truth_context
        self.save_full_generation = save_full_generation
        # Ask the same single call to also name the concrete action that should have
        # replaced the blamed one. Costs no extra call -- it is one more field in a JSON
        # object this attributor already requests. Off by default anyway, because turning it
        # on changes the system prompt, and a changed prompt can move the attribution
        # itself; a caller upgrading agentdebugx must not silently get different blames.
        self.propose_corrected_action = propose_corrected_action
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
        system_prompt = (
            _ATTR_SYSTEM_PROMPT_WITH_ACTION
            if self.propose_corrected_action
            else _ATTR_SYSTEM_PROMPT
        )
        messages = [
            {'role': 'system', 'content': system_prompt},
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
        blame = self._prefer_supported_finding(blame, findings)
        # After normalization: the corrected action is graded against the step we actually
        # ended up blaming, so `original` and `differs_from_original` describe that step and
        # not whichever ordinal the model first named.
        action_reason = 'not_requested'
        if self.propose_corrected_action:
            blame.corrected_action = _build_corrected_action(
                trajectory,
                _event_for_blame(trajectory, blame),
                parsed.get('corrected_action'),
                self.id,
            )
            action_reason = (
                'emitted' if blame.corrected_action is not None else 'declined_by_model'
            )
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
                'corrected_action': _corrected_action_report(
                    requested=self.propose_corrected_action,
                    action=blame.corrected_action,
                    reason=action_reason,
                    source=self.id,
                ),
                **({'full_generation': full_generation} if full_generation else {}),
            },
        )

    @staticmethod
    def _prefer_supported_finding(
        blame: Blame,
        findings: List[FailureFinding],
    ) -> Blame:
        """Keep attribution on the detector's exact event when steps coincide."""

        candidates = [
            finding for finding in findings
            if finding.event_id
            and finding.step_index == blame.step_index
            and (
                not blame.agent_name
                or not finding.agent_name
                or finding.agent_name == blame.agent_name
            )
        ]
        if len(candidates) != 1 or blame.span_id == candidates[0].event_id:
            return blame
        finding = candidates[0]
        return Blame(
            span_id=finding.event_id,
            step_index=finding.step_index,
            agent_name=finding.agent_name or blame.agent_name,
            confidence=blame.confidence,
            rationale=blame.rationale,
            evidence=list(blame.evidence),
            sources=list(blame.sources) + ['detector_event_anchor'],
            # Carried, not dropped: this helper re-anchors WHICH event is blamed, not what
            # should have been done instead. A rebuilt Blame that silently loses the field
            # would look exactly like an attributor that declined to produce one.
            corrected_action=blame.corrected_action,
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
            corrected_action=blame.corrected_action,
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


#: `_STEP_SYSTEM_PROMPT` plus the nullable action field; used only when the caller opted in,
#: so the default prompt stays byte-identical.
_STEP_SYSTEM_PROMPT_WITH_ACTION = """You are an AI assistant tasked with evaluating the correctness of each step in an ongoing agent conversation aimed at solving a real-world problem.

Respond ONLY with a JSON object matching this schema (no prose, no markdown):

{
  "is_failure_step": true | false,
  "confidence": <float in [0,1]>,
  "rationale": "<one sentence>",
  "evidence": ["<short quoted evidence>", ...],
  "corrected_action": {"tool": "<tool name>", "args": {<arguments>}} or null
}

Be CONSERVATIVE: only return true when the trajectory evidence on this step
specifically caused the cascading failure.
Do not rely on information outside the provided trajectory/context.
Return "corrected_action": null whenever "is_failure_step" is false.
""" + _CORRECTED_ACTION_RULES


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
        # See AllAtOnceAttributor above: thinking models can burn the whole
        # budget on reasoning before emitting text.
        max_tokens: int = 16000,
        use_ground_truth_context: bool = False,
        save_full_generation: bool = False,
        propose_corrected_action: bool = False,
    ) -> None:
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_steps = max_steps
        self.context_window = context_window
        self.max_tokens = max_tokens
        self.use_ground_truth_context = use_ground_truth_context
        self.save_full_generation = save_full_generation
        #: One more field in the per-step JSON this attributor already requests -- no extra
        #: call, and no extra call per step either. See AllAtOnceAttributor for why it is off
        #: by default.
        self.propose_corrected_action = propose_corrected_action

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
                corrected_action=(
                    _build_corrected_action(
                        trajectory, evt, verdict.get('corrected_action'), self.id
                    )
                    if self.propose_corrected_action
                    else None
                ),
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
                    'corrected_action': _corrected_action_report(
                        requested=self.propose_corrected_action,
                        action=None,
                        reason=(
                            'no_hypotheses' if self.propose_corrected_action
                            else 'not_requested'
                        ),
                        source=self.id,
                    ),
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
                'corrected_action': _corrected_action_report(
                    requested=self.propose_corrected_action,
                    action=hypotheses[0].corrected_action,
                    reason=(
                        'not_requested' if not self.propose_corrected_action
                        else 'emitted' if hypotheses[0].corrected_action is not None
                        else 'declined_by_model'
                    ),
                    source=self.id,
                ),
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
                        {
                            'role': 'system',
                            'content': (
                                _STEP_SYSTEM_PROMPT_WITH_ACTION
                                if self.propose_corrected_action
                                else _STEP_SYSTEM_PROMPT
                            ),
                        },
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
        max_tokens: int = 16000,
        context_window: Optional[int] = 6,
        always_include_steps: Optional[Sequence[int]] = None,
        use_ground_truth_context: bool = False,
        propose_corrected_action: bool = False,
    ) -> None:
        # max_tokens default doubled in 0.2.4: thinking models (Gemini, o-series)
        # consume most of the budget on reasoning before any JSON is emitted, so
        # 1024 was empirically truncating bisect probes in the v0.2.3 E2E.
        # Raised again (4096 -> 16000): a real Terminal-Bench trajectory measured
        # reasoning_tokens=7866 for a single analogous attributor call against
        # gemini-3.1-pro through the AGENTDEBUG_LLM_BASE_URL proxy, so 4096 still
        # truncated mid-reasoning before any visible text was emitted.
        self.llm = llm
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_tokens = max_tokens
        # When formatting a prefix into the LLM prompt we keep this many events at the
        # head plus this many at the tail; the middle is elided to bound cost on long
        # trajectories. The elision is POSITIONAL, not relevance-based, so on a
        # 30-event trajectory at the default of 6 the attributor sees steps 0-5 and
        # 24-29 and is blind to 18 of 30 -- a constraint established mid-trajectory
        # cannot be seen at all, however decisive it is.
        #
        # Two ways out, both opt-in so the default rendering is byte-identical:
        #   * `always_include_steps` pins specific step indices into every rendered
        #     window. A caller that already suspects a step -- a detector finding, a
        #     judge's checkpoint, ground truth -- can guarantee the attributor sees it.
        #   * `context_window=None` disables elision entirely.
        self.context_window = context_window
        self.always_include_steps = (
            frozenset(always_include_steps) if always_include_steps is not None else frozenset()
        )
        self.use_ground_truth_context = use_ground_truth_context
        # A bisect probe answers "upper or lower" -- there is nowhere in that schema to put
        # an action, so a correction costs ONE extra call after the search converges. O(log
        # n) + 1, and only when asked. Off by default: nobody's bill changes on upgrade.
        self.propose_corrected_action = propose_corrected_action

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
        corrected: Optional[CorrectedAction] = None
        action_reason = 'not_requested'
        if self.propose_corrected_action:
            gt = _ground_truth_text(trajectory)
            corrected, action_reason = _request_corrected_action(
                self.llm,
                trajectory,
                decisive,
                source=self.id,
                max_tokens=self.max_tokens,
                extra_context=(
                    f'The Answer for the problem is: {gt}\n'
                    if self.use_ground_truth_context and gt
                    else ''
                ),
            )
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
                corrected_action=corrected,
            )],
            raw={
                'probe_count': probe_count,
                'corrected_action': _corrected_action_report(
                    requested=self.propose_corrected_action,
                    action=corrected,
                    reason=action_reason,
                    source=self.id,
                ),
                'trajectory_len': n,
                'ground_truth_context_used': bool(
                    self.use_ground_truth_context and _ground_truth_text(trajectory)
                ),
                'search_strategy': 'half_recursive',
                # Whether prompt rendering could withhold steps from the probes, and
                # which were pinned in regardless. Reported for the full trajectory:
                # individual probes render shorter prefixes and elide no more than this.
                'context_elision': self._prefix_view(list(trajectory.events))[1],
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

    def _prefix_view(self, events: List[Any]) -> Tuple[List[Any], Dict[str, Any]]:
        """The events a probe will actually see, plus a report of what was hidden.

        Split out from `_render_prefix` so `attribute` can tell the caller whether any
        step was withheld. Without that, a downstream harness cannot distinguish "the
        attributor considered this step and dismissed it" from "the attributor never
        saw it", which are very different findings about the same trajectory.
        """
        window = self.context_window
        pinned = sorted(
            {e.step_index for e in events if getattr(e, 'step_index', None) in self.always_include_steps}
        )
        if window is None or len(events) <= 2 * window:
            return list(events), {
                'elided': False,
                'events_total': len(events),
                'events_shown': len(events),
                'context_window': window,
                'pinned_steps': pinned,
            }

        head = events[:window]
        tail = events[-window:]
        kept_ids = {id(e) for e in head} | {id(e) for e in tail}
        # Pinned events are restored in trajectory order, so the prompt stays monotone
        # in step index rather than appending them out of sequence.
        middle = [
            e
            for e in events[window:-window]
            if getattr(e, 'step_index', None) in self.always_include_steps
        ]
        restored = [e for e in middle if id(e) not in kept_ids]
        elided = len(events) - 2 * window - len(restored)
        view: List[Any] = list(head)
        if restored:
            view.extend(restored)
        if elided > 0:
            view.append(_EVENT_ELLIPSIS(elided))
        view.extend(tail)
        return view, {
            'elided': elided > 0,
            'events_total': len(events),
            'events_shown': len(events) - elided,
            'events_elided': elided,
            'context_window': window,
            'pinned_steps': pinned,
            'restored_by_pinning': len(restored),
        }

    def _render_prefix(self, prefix: AgentTrajectory) -> str:
        view, _ = self._prefix_view(list(prefix.events))
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


# ---------------------------------------------------------------------------
# Corrected actions
#
# Everything below is opt-in. With `propose_corrected_action=False` (the default on every
# attributor) none of it runs, no prompt changes by a single byte, and no extra token is
# spent -- so an existing caller sees exactly the behaviour it saw before.
# ---------------------------------------------------------------------------

_PROPOSE_ACTION_SYSTEM_PROMPT = (
    'You are correcting ONE step of a failed agent trajectory.\n\n'
    'You are given the goal, the trajectory, and the single step already identified as the '
    'decisive mistake. Name the one concrete action that should have been taken INSTEAD at '
    'that step.\n\n'
    'Respond ONLY with a JSON object (no prose, no markdown):\n\n'
    '{"corrected_action": {"tool": "<tool name>", "args": {<arguments>}}}\n\n'
    'or, when you cannot name one:\n\n'
    '{"corrected_action": null}\n'
    + _CORRECTED_ACTION_RULES
)


def _normalize_action(value: Any) -> Optional[Dict[str, Any]]:
    """Coerce a model-emitted action into the trace's own `{'tool', 'args'}` shape.

    Strict on `args`: it must be a JSON object or absent. A string or a list cannot be
    replayed into a `tool.call` input without inventing a key for it, and an invented key is
    exactly the kind of plausible-but-wrong artifact this whole field exists to avoid --
    so we return None (honestly: no action) rather than reshape it.
    """
    if not isinstance(value, dict):
        return None
    tool = value.get('tool') or value.get('name') or value.get('action')
    if not isinstance(tool, str) or not tool.strip():
        return None
    args: Any = None
    for key in ('args', 'arguments', 'parameters'):
        if key in value:
            args = value[key]
            break
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return {'tool': tool.strip(), 'args': dict(args)}


def _original_action_at(
    trajectory: AgentTrajectory,
    event: Optional[AgentEvent],
) -> Optional[Dict[str, Any]]:
    """The trace's OWN action at the blamed step, in the same shape, or None if it had none.

    Needed to answer `differs_from_original` truthfully. The blamed event is frequently the
    agent's thought (`agent.step`) rather than the call it issued, so when the event carries
    no action of its own we look for the sibling `tool.call` at the same step index.
    """
    if event is None:
        return None
    direct = _normalize_action(event.input)
    if direct is not None:
        return direct
    if event.step_index is None:
        return None
    for candidate in trajectory.events:
        if candidate.step_index != event.step_index:
            continue
        if getattr(candidate.event_type, 'value', candidate.event_type) != 'tool.call':
            continue
        if (
            event.agent_name
            and candidate.agent_name
            and candidate.agent_name != event.agent_name
        ):
            continue
        found = _normalize_action(candidate.input)
        if found is not None:
            return found
    return None


def _event_for_blame(
    trajectory: AgentTrajectory,
    blame: Blame,
) -> Optional[AgentEvent]:
    by_id = {e.event_id: e for e in trajectory.events}
    if blame.span_id and blame.span_id in by_id:
        return by_id[blame.span_id]
    if blame.step_index is not None:
        for candidate in trajectory.events:
            if candidate.step_index == blame.step_index:
                return candidate
    return None


def _build_corrected_action(
    trajectory: AgentTrajectory,
    event: Optional[AgentEvent],
    value: Any,
    source: str,
) -> Optional[CorrectedAction]:
    """A CorrectedAction from a model-emitted value, or None if it is not usable as one."""
    action = _normalize_action(value)
    if action is None:
        return None
    original = _original_action_at(trajectory, event)
    return CorrectedAction(
        tool=action['tool'],
        args=action['args'],
        source=source,
        original=original,
        # None, not False, when the step has no action of its own: "differs" has no answer
        # there, and reporting False would claim the correction is a no-op when we don't know.
        differs_from_original=None if original is None else original != action,
    )


def _corrected_action_report(
    *,
    requested: bool,
    action: Optional[CorrectedAction],
    reason: str,
    source: str,
) -> Dict[str, Any]:
    """Machine-readable "why is there no corrected action", for `AttributionResult.raw`.

    Same defect class as #3's empty-result explanation: absent must not be silent, or a
    consumer cannot tell "not asked" from "asked and honestly declined".
    """
    return {
        'requested': requested,
        'emitted': action is not None,
        'reason': reason,
        'source': source,
        'differs_from_original': action.differs_from_original if action else None,
    }


def _request_corrected_action(
    llm: LLMClient,
    trajectory: AgentTrajectory,
    event: Optional[AgentEvent],
    *,
    source: str,
    # Raised 4096 -> 16000: a real Terminal-Bench trajectory measured
    # reasoning_tokens=7866 for an analogous call, so 4096 still truncated.
    max_tokens: int = 16000,
    extra_context: str = '',
) -> Tuple[Optional[CorrectedAction], str]:
    """One focused follow-up call for attributors whose own probes have no slot for it.

    BinarySearch answers "upper or lower"; Counterfactual answers "would it have been
    rescued". Neither has a place to put an action, so the correction costs one extra call
    -- charged only when the caller asked for it. Returns (action, reason).

    `max_tokens` defaults high and callers pass their own, for the reason the bisect probe's
    default was doubled in 0.2.4: thinking models spend most of the budget reasoning before
    any JSON appears, and a truncated response here is indistinguishable from a model that
    honestly declined -- which would quietly turn a cost problem into a wrong measurement.
    """
    if event is None:
        return None, 'no_blamed_event'
    events_doc = '\n'.join(
        f'Step {e.step_index if e.step_index is not None else "?"} '
        f'[{e.event_id}] {e.agent_name} '
        f'({getattr(e.event_type, "value", e.event_type)}): '
        f'{(str(e.output) if e.output is not None else str(e.input or ""))[:300]}'
        f'{(" | ERROR: " + str(e.error)) if e.error else ""}'
        for e in trajectory.events
    ) or '(empty conversation)'
    original = _original_action_at(trajectory, event)
    user = (
        f'GOAL: {trajectory.goal or "(unknown goal)"}\n'
        f'{extra_context}'
        f'\nTRAJECTORY:\n{events_doc}\n\n'
        'THE DECISIVE MISTAKE IS THIS STEP:\n'
        f'  event_id={event.event_id}\n'
        f'  step={event.step_index} agent={event.agent_name}\n'
        f'  type={getattr(event.event_type, "value", event.event_type)}\n'
        f'  action_taken={original if original is not None else "(this step took no action)"}\n'
        f'  output={str(event.output)[:300]}\n'
        f'  error={str(event.error) if event.error else ""}\n'
    )
    try:
        result = llm.complete(
            messages=[
                {'role': 'system', 'content': _PROPOSE_ACTION_SYSTEM_PROMPT},
                {'role': 'user', 'content': user},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:  # pragma: no cover - defensive
        LOG.warning('corrected-action proposal failed for event=%s: %s', event.event_id, exc)
        return None, 'llm_error'
    parsed = extract_json_block(result.text)
    if parsed is None:
        return None, 'unparseable_response'
    action = _build_corrected_action(
        trajectory, event, parsed.get('corrected_action'), source
    )
    if action is None:
        return None, 'declined_by_model'
    return action, 'emitted'


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
        # See AllAtOnceAttributor above: thinking models can burn the whole
        # budget on reasoning before emitting text.
        max_tokens: int = 16000,
        fallback: Optional[Attributor] = None,
        propose_corrected_action: bool = False,
    ) -> None:
        self.llm = llm
        self.max_candidates = max_candidates
        self.max_tokens = max_tokens
        self.fallback: Attributor = fallback or HeuristicAttributor()
        # ONE extra call on the winning candidate, not K. The per-candidate probe asks for a
        # rescue probability; only the top-ranked step is worth naming a replacement for,
        # and asking all K would multiply the cost of the field by the candidate budget.
        self.propose_corrected_action = propose_corrected_action

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
        action_reason = 'not_requested'
        if self.propose_corrected_action:
            corrected, action_reason = _request_corrected_action(
                self.llm, trajectory, ranked[0][0], source=self.id,
                max_tokens=self.max_tokens,
            )
            hypotheses[0].corrected_action = corrected
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            raw={
                'candidates_probed': len(ranked),
                'corrected_action': _corrected_action_report(
                    requested=self.propose_corrected_action,
                    action=hypotheses[0].corrected_action,
                    reason=action_reason,
                    source=self.id,
                ),
            },
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
                # Statistics over a corpus of signatures: this attributor can say which step
                # is suspicious but has no model to say what should have been done instead,
                # so it reports the absence rather than inventing an action.
                'corrected_action': _corrected_action_report(
                    requested=False, action=None, reason='no_llm', source=self.id,
                ),
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
                # The ensemble never asks for a correction itself; it forwards whatever its
                # backends produced. Reported here so the same key means the same thing on
                # every attributor, and `source` names the backend that actually offered it.
                'corrected_action': _corrected_action_report(
                    requested=False,
                    action=merged[0].corrected_action if merged else None,
                    reason=(
                        'forwarded_from_backend'
                        if merged and merged[0].corrected_action is not None
                        else 'no_backend_offered_one'
                    ),
                    source=(
                        merged[0].corrected_action.source
                        if merged and merged[0].corrected_action is not None
                        else self.id
                    ),
                ),
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
                self._absorb_corrected_action(row, h)
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
                self._absorb_corrected_action(row, h)
        ranked = sorted(
            agg.values(),
            key=lambda r: (-(1.0 - r.bayesian_not_pos), -r.weighted_conf_sum),
        )
        return [self._row_to_blame(r) for r in ranked]

    @staticmethod
    def _absorb_corrected_action(row: '_MergeRow', hypothesis: Blame) -> None:
        """Keep the corrected action from the most confident backend that offered one.

        Deterministic and provenance-preserving: backends that produced nothing never
        overwrite one that did, and the winner's `source` still names the backend it came
        from, so a merged action is never mistaken for ensemble consensus about the action.
        """
        action = hypothesis.corrected_action
        if action is None:
            return
        if row.corrected_action is None or hypothesis.confidence > row.corrected_action_conf:
            row.corrected_action = action
            row.corrected_action_conf = hypothesis.confidence

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
            corrected_action=row.corrected_action,
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
    #: Best corrected action seen for this step, with the confidence of the hypothesis that
    #: carried it. Rationales merge by concatenation; an action cannot -- two different tool
    #: calls have no meaningful average -- so the ensemble keeps ONE and records whose it was
    #: in `CorrectedAction.source`.
    corrected_action: Optional[CorrectedAction] = None
    corrected_action_conf: float = -1.0


__all__ = [
    'AllAtOnceAttributor', 'AttributionBudget', 'AttributionResult',
    'Attributor', 'BinarySearchAttributor', 'Blame', 'CorrectedAction',
    'CounterfactualAttributor', 'EnsembleAttributor', 'HeuristicAttributor',
    'SBFLAttributor', 'StepByStepAttributor',
]
