"""State-aware attribution, ported from TrajDebug's phase C.

The default :class:`~agentdebug.diagnose.attribute.attribution.HeuristicAttributor`
sorts findings by step index and takes the first. That is right only if every
candidate is equally live, and they are not. An agent that commits an error at
step 3, notices at step 39, corrects, and then fails at step 60 for an unrelated
reason will still have step 3 blamed, because nothing in the pipeline asks
whether the agent recovered.

This runs the three phases TrajDebug uses to answer that, in the same order:

* **C1 -- cluster.** Group findings by the concrete thing they violate rather
  than by taxonomy label, so repeated symptoms of one error do not each count
  as a separate candidate. Deterministic; no model involved.

  This is a **weaker approximation of TrajDebug's C1 than it may look**, and the
  gap is measured rather than assumed. Over 100 ALFWorld trajectories (2,960
  triggers), their LLM-backed clustering compressed 29.6 triggers into 11.0
  instances (2.7x); grouping on exact reference-quote text compresses to 22.7
  (1.3x), and agrees with their instance count within +/-2 on only 5 of 100
  trajectories. Containment and shingle-Jaccard variants were tried and did not
  help (23.5 and 21.7 respectively).

  The reason is that the detector quotes differently-worded spans for the same
  underlying violated object, and their model merges those semantically. String
  similarity does not reach it. So this rule reliably collapses *exact* repeats
  and little else. Closing the gap means an LLM-backed clustering pass, which
  would match their design; it is deliberately not done here, so that C1 stays
  free and the no-LLM path keeps working.
* **C2 -- state.** For each instance, decide whether the agent ever fixed it,
  whether it actually reaches the terminal failure, and how. Needs a model, and
  is skipped when none is supplied.
* **C3 -- select.** Take the earliest instance still in the causal chain.
  Deterministic, so the model proposes and a rule disposes.

With no ``llm`` this degrades to C1 + C3: clustering plus earliest-in-chain,
which is still strictly more than the heuristic attributor does and costs
nothing. That mode is the reason C1 and C3 were kept model-free.

Ported from TrajDebug (THU-KEG/TrajDebug, MIT), phases
``stage_c_phase1_cluster`` / ``stage_c_phase2_state`` / ``stage_c_phase3_assemble``.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from agentdebug.diagnose.attribute.attribution import AttributionResult, Blame
from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.schema import (
    AgentTrajectory,
    FailureFinding,
    confidence_or_default,
)

LOG = logging.getLogger('agentdebug.attribute.trajdebug')

_WS = re.compile(r'\s+')

_STATE_PROMPT = """You are assessing what became of an error in an agent run.

For each error instance you are given: where it started, the wrong commitment,
and what it violated. Decide three things, using ONLY the trajectory shown.

1. fix_status -- did the agent later correct this itself? If yes, answer
   "fixed_at_step_N" and quote the text showing it in fix_evidence_quote,
   verbatim. If it was never corrected, answer null and leave the quote null.
   Do not invent a correction; an unfixed error is the normal case.

2. chain_membership -- is this error part of why the run ultimately failed?
   false means the run failed for some other reason and this instance is a
   distraction, however wrong it was.

3. terminal_connection -- how it reached the ending. Use "budget_debt" when the
   error never produced a wrong result but consumed steps or tokens the run
   needed. Use null when chain_membership is false.

Output ONLY a JSON object. No prose, no markdown fences.

{"instances":[{"instance_id":N, "fix_status":"fixed_at_step_N"|null,
  "fix_evidence_quote":"..."|null, "chain_membership":true|false,
  "terminal_connection":"budget_debt"|"..."|null,
  "wasted_steps":[N,...]}, ...]}
"""


class _Instance:
    """One clustered error: several findings violating the same thing."""

    __slots__ = ('instance_id', 'findings', 'violated')

    def __init__(self, instance_id: int, violated: str) -> None:
        self.instance_id = instance_id
        self.violated = violated
        self.findings: List[FailureFinding] = []

    @property
    def origin_step(self) -> Optional[int]:
        steps = [f.step_index for f in self.findings if f.step_index is not None]
        return min(steps) if steps else None

    @property
    def last_step(self) -> Optional[int]:
        steps = [f.step_index for f in self.findings if f.step_index is not None]
        return max(steps) if steps else None

    @property
    def origin(self) -> FailureFinding:
        """The earliest finding, which is the one the instance is blamed at."""

        return sorted(
            self.findings,
            key=lambda f: (
                f.step_index is None,
                f.step_index if f.step_index is not None else 10**9,
                -confidence_or_default(f.confidence),
            ),
        )[0]


class TrajDebugAttributor:
    """Attribute stage component implementing cluster -> state -> select."""

    id = 'trajdebug'

    #: Ranks findings; it does not derive them. See HeuristicAttributor for why
    #: this distinction is worth exposing rather than returning a silent empty.
    requires_findings: bool = True

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        *,
        max_tokens: int = 4096,
        max_instances_scored: int = 12,
        rank_policy: str = 'earliest',
    ) -> None:
        #: Optional. Without it, C2 is skipped and instances carry no state.
        self.llm = llm
        self.max_tokens = max_tokens
        self.max_instances_scored = max_instances_scored
        #: How C3 breaks the tie between in-chain instances. ``'earliest'`` is
        #: the original policy; ``'confident'`` ranks on the detector's own
        #: confidence instead of on position. Defaults to the original.
        if rank_policy not in ('earliest', 'confident'):
            raise ValueError(
                f'unknown rank_policy {rank_policy!r}; expected earliest or confident'
            )
        self.rank_policy = rank_policy

    # -- public API --------------------------------------------------------
    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        started = time.time()
        findings = findings or []
        if not findings:
            return AttributionResult(
                method=self.id,
                hypotheses=[],
                raw={
                    'reason': 'no_findings_supplied',
                    'detail': (
                        'TrajDebugAttributor clusters and ranks detector findings and '
                        'cannot derive them. Call it through DiagnosePipeline, or pass '
                        'findings= explicitly.'
                    ),
                    'requires_findings': True,
                },
            )

        instances = self._cluster(findings)
        states: Dict[int, Dict[str, Any]] = {}
        if self.llm is not None:
            states = self._classify_states(trajectory, instances)

        hypotheses = [self._blame_for(inst, states.get(inst.instance_id, {}))
                      for inst in self._rank(instances, states)]

        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            elapsed_ms=int((time.time() - started) * 1000),
            raw={
                'num_findings': len(findings),
                'num_instances': len(instances),
                'state_classified': self.llm is not None,
                'clusters': [
                    {
                        'instance_id': inst.instance_id,
                        'origin_step': inst.origin_step,
                        'last_step': inst.last_step,
                        'num_findings': len(inst.findings),
                        'violated': inst.violated[:200],
                    }
                    for inst in instances
                ],
            },
        )

    # -- C1: cluster -------------------------------------------------------
    def _cluster(self, findings: Sequence[FailureFinding]) -> List[_Instance]:
        """Group findings by the concrete object they violate.

        Keyed on ``reference_quote`` -- the actual text the step contradicts --
        rather than on the taxonomy label, because two genuinely different
        errors often share a label while twenty repetitions of one error share
        the violated text. Findings with no reference quote cannot be grouped
        this way and each become their own instance, which is the conservative
        choice: wrongly merging two errors hides one of them.
        """

        by_key: Dict[str, _Instance] = {}
        instances: List[_Instance] = []

        for finding in findings:
            reference = (finding.reference_quote or '').strip()
            if reference:
                key = _WS.sub(' ', reference).lower()
            else:
                key = f'__ungrouped__{finding.finding_id}'

            instance = by_key.get(key)
            if instance is None:
                instance = _Instance(len(instances), reference or '(no reference quote)')
                by_key[key] = instance
                instances.append(instance)
            instance.findings.append(finding)

        return instances

    # -- C2: state ---------------------------------------------------------
    def _classify_states(
        self, trajectory: AgentTrajectory, instances: Sequence[_Instance]
    ) -> Dict[int, Dict[str, Any]]:
        scored = list(instances)[: self.max_instances_scored]
        if not scored:
            return {}

        instance_doc = '\n'.join(
            f'- instance_id={inst.instance_id} origin_step={inst.origin_step} '
            f'last_step={inst.last_step} '
            f'wrong={(inst.origin.wrong_content_quote or "")[:300]!r} '
            f'violated={inst.violated[:300]!r}'
            for inst in scored
        )
        steps_doc = '\n'.join(
            f'[step {e.step_index}] {str(e.output or "")[:600]}'
            for e in trajectory.events
        )
        messages = [
            {'role': 'system', 'content': _STATE_PROMPT},
            {'role': 'user', 'content': (
                f'GOAL: {trajectory.goal!r}\n\n'
                f'ERROR INSTANCES:\n{instance_doc}\n\n'
                f'TRAJECTORY:\n{steps_doc}\n'
            )},
        ]

        try:
            result = self.llm.complete(messages=messages, max_tokens=self.max_tokens)
        except Exception as exc:  # pragma: no cover - transport failure
            LOG.warning('state classification failed, continuing without it: %s', exc)
            return {}

        parsed = extract_json_block(result.text)
        if not parsed:
            LOG.warning('state classifier returned no JSON; continuing without state')
            return {}

        states: Dict[int, Dict[str, Any]] = {}
        for raw in parsed.get('instances') or []:
            if not isinstance(raw, dict):
                continue
            instance_id = raw.get('instance_id')
            if not isinstance(instance_id, int):
                continue
            states[instance_id] = {
                'fix_status': self._opt_str(raw.get('fix_status')),
                'fix_evidence_quote': self._opt_str(raw.get('fix_evidence_quote')),
                'chain_membership': (
                    raw['chain_membership']
                    if isinstance(raw.get('chain_membership'), bool)
                    else None
                ),
                'terminal_connection': self._opt_str(raw.get('terminal_connection')),
                'wasted_steps': [
                    s for s in (raw.get('wasted_steps') or []) if isinstance(s, int)
                ],
            }
        return states

    # -- C3: select --------------------------------------------------------
    def _rank(
        self, instances: Sequence[_Instance], states: Dict[int, Dict[str, Any]]
    ) -> List[_Instance]:
        """Rank the instances, chain membership first, then ``rank_policy``.

        An instance the model explicitly ruled out of the chain sorts last
        however early it is -- that is the whole point of running C2. An
        instance with no state (no model, or the model skipped it) is treated
        as in-chain, so absence of evidence does not silently exclude it.

        Within the chain, ``'earliest'`` takes the first instance by origin
        step, the original policy. ``'confident'`` takes the instance the
        detector was most sure of. The distinction only matters when several
        instances survive C2 -- but that is the common case, so which tiebreak
        runs is worth being able to vary and measure.
        """
        prefer_confidence = self.rank_policy == 'confident'

        def key(inst: _Instance) -> tuple:
            state = states.get(inst.instance_id, {})
            out_of_chain = state.get('chain_membership') is False
            origin = inst.origin_step
            position = (origin is None, origin if origin is not None else 10**9)
            confidence = -confidence_or_default(inst.origin.confidence)
            if prefer_confidence:
                return (out_of_chain, confidence, *position)
            return (out_of_chain, *position, confidence)

        return sorted(instances, key=key)

    def _blame_for(self, instance: _Instance, state: Dict[str, Any]) -> Blame:
        origin = instance.origin
        supporting = len(instance.findings)
        return Blame(
            span_id=origin.event_id,
            step_index=origin.step_index,
            agent_name=origin.agent_name,
            confidence=confidence_or_default(origin.confidence),
            rationale=(
                f'{origin.failure_mode.name} at step {origin.step_index}. '
                f'{supporting} finding(s) violate the same thing: '
                f'{instance.violated[:160]!r}'
            ),
            evidence=[q for q in (origin.wrong_content_quote, origin.reference_quote) if q],
            sources=[self.id],
            fix_status=state.get('fix_status'),
            fix_evidence_quote=state.get('fix_evidence_quote'),
            chain_membership=state.get('chain_membership'),
            terminal_connection=state.get('terminal_connection'),
            wasted_steps=list(state.get('wasted_steps') or []),
        )

    @staticmethod
    def _opt_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


__all__ = ['TrajDebugAttributor']
