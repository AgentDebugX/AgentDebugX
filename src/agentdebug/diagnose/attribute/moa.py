"""mixture_of_agents - N independent proposers, then one summarizing agent.

The shape, in one line: run N attributors on the SAME failed trajectory without
letting any of them see another's answer, sort their proposals by an explicit
key, and hand all of them to a separate summarizer call that returns ONE
diagnosis in the ordinary :class:`Blame` schema.

How this differs from what the package already has:

* :class:`~agentdebug.diagnose.attribute.attribution.EnsembleAttributor` merges
  several backends by arithmetic in Python (Borda points, or a Bayesian
  combination of confidences). No model ever sees the other backends' answers.
* :class:`~agentdebug.diagnose.attribute.moe.AaoMoeAttributor` is
  mixture-of-EXPERTS: exactly two structurally different readings plus a
  tie-break call, and the second expert is gated on the trace's structure.
* This module is mixture-of-AGENTS: N interchangeable proposers (usually the
  same method against different models, or one model at several seeds) plus a
  summarizer STAGE that is a real model call. It generalises a plain consensus
  vote, and the difference from a vote is the point: the summarizer can reject
  the majority, and can name a step no proposer named. Both of those outcomes
  are recorded rather than smoothed away.

WHAT THIS DOES NOT CLAIM. Agreement between proposers is not a correctness
label, and neither is the summary. Nothing here verifies a diagnosis against
the environment, a gold label, or a rerun; N models can agree and be wrong
together, most easily when they share a family, a prompt, or a training
corpus. ``summary_agrees_with_proposers`` is a measurement of the panel, not
evidence about the failure. Treat the output as a hypothesis carrying its own
audit trail: read ``AttributionResult.raw['mixture_of_agents']`` and decide.

Provenance is the reason to use this over a vote. Every proposal is kept with
the proposer that made it, the model behind it, the seed it was given (and
whether that seed could actually be applied), its decisive step, and its
status when it produced nothing. The summary records whether its decisive step
matches any proposer's, so a summary that agrees with nobody is visible in the
result instead of being hidden by a merge.

Degradation is explicit at every stage. One proposer that raises, times out, or
returns unparseable text is recorded as a dropout and the panel continues with
the rest. A summarizer that fails does not invent an answer: the result falls
back to the most-supported proposal, and ``summary_source`` says
``'degraded_top_proposal'`` so a consumer never mistakes it for a summarized
diagnosis.
"""

from __future__ import annotations

import copy
import inspect
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentdebug.runtime import LLMClient, extract_json_block
from agentdebug.runtime.llm import CompletionResult, TokenUsage
from agentdebug.schema import AgentTrajectory, FailureFinding

from agentdebug.diagnose.attribute.attribution import (
    NO_FALLBACK,
    AllAtOnceAttributor,
    AttributionResult,
    AttributionUnavailable,
    Attributor,
    Blame,
    HeuristicAttributor,
)

LOG = logging.getLogger('agentdebug.attribution.moa')

#: A proposer produced a usable hypothesis.
STATUS_OK = 'ok'
#: The proposer ran but returned no hypothesis at all.
STATUS_EMPTY = 'empty'
#: The proposer declined: the model errored or its reply carried no JSON, and
#: the proposer was configured fail-closed (``fallback=NO_FALLBACK``).
STATUS_UNAVAILABLE = 'unavailable'
#: The proposer raised something else. Recorded, never re-raised.
STATUS_ERROR = 'error'

#: The final diagnosis came from the summarizer call, as intended.
SUMMARY_FROM_SUMMARIZER = 'summarizer'
#: The summarizer failed or returned no JSON; the most-supported proposal was
#: promoted instead. NOT a summarized diagnosis.
SUMMARY_DEGRADED = 'degraded_top_proposal'
#: No proposer produced anything, so the configured fallback attributor ran.
SUMMARY_FALLBACK = 'fallback_no_proposals'


@dataclass(frozen=True)
class ProposerSpec:
    """One proposer: an attributor, plus the provenance needed to audit it.

    ``attributor`` is any :class:`Attributor`. It is normally an
    :class:`AllAtOnceAttributor` bound to one model, but a panel may mix
    methods, and nothing here requires the proposers to be alike.

    ``model`` and ``seed`` are recorded, not enforced. They describe what the
    caller bound into ``attributor``, and they are what makes two proposals
    over the same model at different seeds distinguishable afterwards. Build
    specs with :func:`proposers_from_clients` or :func:`proposers_from_seeds`
    to have them filled in consistently.
    """

    proposer_id: str
    attributor: Attributor
    model: str = ''
    seed: Optional[int] = None
    #: False when a seed was requested but the client had no way to send one.
    #: Reported so provenance never claims a seed that never left the process.
    seed_applied: bool = False

    def describe(self) -> Dict[str, Any]:
        """Identity of this proposer, without the attributor object itself."""

        return {
            'proposer_id': self.proposer_id,
            'method': getattr(self.attributor, 'id', ''),
            'model': self.model,
            'seed': self.seed,
            'seed_applied': self.seed_applied,
        }


@dataclass(frozen=True)
class Proposal:
    """One proposer's answer, or the reason it does not have one.

    ``blame`` is the proposal itself, kept whole rather than reduced to a step
    number, so a caller can re-read the rationale and evidence the summary was
    built from. It is None for every status other than :data:`STATUS_OK`.
    """

    proposer_id: str
    model: str
    seed: Optional[int]
    status: str
    method: str = ''
    blame: Optional[Blame] = None
    elapsed_ms: int = 0
    error: str = ''
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def step_index(self) -> Optional[int]:
        """The decisive step this proposer named, or None."""

        return self.blame.step_index if self.blame is not None else None

    @property
    def span_id(self) -> Optional[str]:
        """The event this proposer blamed, or None."""

        return self.blame.span_id if self.blame is not None else None

    @property
    def confidence(self) -> float:
        """The proposer's own confidence. 0.0 when it produced nothing."""

        return self.blame.confidence if self.blame is not None else 0.0

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe record for ``AttributionResult.raw``."""

        return {
            'proposer_id': self.proposer_id,
            'method': self.method,
            'model': self.model,
            'seed': self.seed,
            'status': self.status,
            'step_index': self.step_index,
            'span_id': self.span_id,
            'agent_name': self.blame.agent_name if self.blame else None,
            'confidence': self.confidence,
            'rationale': self.blame.rationale if self.blame else '',
            'evidence': list(self.blame.evidence) if self.blame else [],
            'elapsed_ms': self.elapsed_ms,
            'error': self.error,
            'usage': asdict(self.usage),
        }


@dataclass(frozen=True)
class SummaryProvenance:
    """Where the final diagnosis came from and who, if anyone, agreed with it.

    ``agrees_with_any_proposer`` is False when the summarizer named a step no
    proposer named. That is a legitimate outcome, not an error, and it is
    surfaced here rather than repaired, because a summary nobody proposed is
    exactly the case a reader needs to look at.
    """

    summary_source: str
    summarizer_model: str = ''
    #: Proposers whose decisive step equals the summary's.
    matched_proposer_ids: List[str] = field(default_factory=list)
    #: Proposers who blamed the summary's exact event id. A subset of the above
    #: whenever both are populated; kept apart because a step can hold several
    #: events and matching one of them is a stronger claim.
    span_matched_proposer_ids: List[str] = field(default_factory=list)
    agrees_with_any_proposer: bool = False
    #: Every usable proposer named the same step. False with zero or one.
    proposers_unanimous: bool = False
    #: Decisive step -> number of proposers who named it. Keys are strings so
    #: the payload survives a JSON round trip unchanged.
    step_votes: Dict[str, int] = field(default_factory=dict)
    #: Proposer ids in the exact order the summarizer saw them.
    proposal_order: List[str] = field(default_factory=list)
    proposers_run: int = 0
    proposers_usable: int = 0
    summarizer_error: str = ''

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe record for ``AttributionResult.raw``."""

        return asdict(self)


@dataclass(frozen=True)
class MixtureOfAgentsDiagnosis:
    """Typed result of one panel run: the proposals, the summary, the audit."""

    proposals: List[Proposal]
    summary: Optional[Blame]
    provenance: SummaryProvenance
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_ms: int = 0

    def usable_proposals(self) -> List[Proposal]:
        """Proposals that carry a hypothesis, in the order the summarizer saw."""

        return [p for p in self.proposals if p.status == STATUS_OK and p.blame is not None]

    def as_raw_payload(self) -> Dict[str, Any]:
        """The mapping stored under ``AttributionResult.raw['mixture_of_agents']``."""

        return {
            'proposals': [proposal.as_dict() for proposal in self.proposals],
            'provenance': self.provenance.as_dict(),
            'usage': asdict(self.usage),
            'elapsed_ms': self.elapsed_ms,
            'disclaimer': (
                'Proposer agreement is not a correctness label. The summary is a '
                'hypothesis produced by one model reading other models.'
            ),
        }


def proposal_sort_key(proposal: Proposal) -> Tuple[int, int, float, str]:
    """The ONE ordering used before proposals reach the summarizer.

    Sorted by decisive step (steps first, unlocalized proposals last), then by
    descending confidence, then by ``proposer_id``. The last component makes the
    order total, so the summarizer prompt depends only on the proposals
    themselves: declaration order, completion order and dict iteration cannot
    move it, and the same panel over the same trajectory builds a byte-identical
    prompt.
    """

    step = proposal.step_index
    return (
        1 if step is None else 0,
        step if step is not None else 0,
        -proposal.confidence,
        proposal.proposer_id,
    )


class SeededClient:
    """An :class:`LLMClient` that pins one seed and temperature onto every call.

    The point is the panel's second configuration: the same model N times, one
    seed each. ``complete`` on the client protocol has no ``seed`` parameter, so
    this binds it once, by the first route the wrapped client supports:

    1. the client's ``complete`` accepts a ``seed`` keyword, or ``**kwargs``;
    2. the client exposes an ``extra_body`` mapping (``OpenAICompatClient``
       does), in which case a shallow copy carrying ``{'seed': ...}`` is used so
       the caller's own client is never mutated.

    If neither route exists, :attr:`seed_applied` is False and no seed is sent.
    Nothing raises and nothing pretends: the panel copies that flag into the
    provenance, so a run whose seeds never reached the provider cannot be read
    later as a seeded run.

    Temperature is forwarded on every call, because N identical samples at
    temperature 0 are N copies of one opinion, not a panel.
    """

    def __init__(
        self,
        inner: LLMClient,
        *,
        seed: Optional[int] = None,
        temperature: float = 1.0,
    ) -> None:
        self.seed = seed
        self.temperature = temperature
        #: False when a seed was asked for and no route existed to send it.
        self.seed_applied = False
        #: The wrapped client, or a seeded shallow copy of it.
        self.inner: LLMClient = inner
        self._seed_kwarg = False
        if seed is not None:
            self._bind_seed(inner, seed)
        #: The model id behind this client, or '' when it does not expose one.
        self.model = str(getattr(self.inner, 'model', '') or '')

    def _bind_seed(self, inner: LLMClient, seed: int) -> None:
        if self._accepts_seed_kwarg(inner):
            self._seed_kwarg = True
            self.seed_applied = True
            return
        if hasattr(inner, 'extra_body'):
            extra_body = getattr(inner, 'extra_body', None)
            merged: Dict[str, Any] = dict(extra_body) if isinstance(extra_body, dict) else {}
            merged['seed'] = seed
            clone: Any = copy.copy(inner)
            clone.extra_body = merged
            self.inner = clone
            self.seed_applied = True
            return
        LOG.info(
            'client %s cannot carry a seed; proposer runs unseeded',
            type(inner).__name__,
        )

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> CompletionResult:
        """Delegate to the wrapped client with this proposer's seed/temperature."""

        kwargs: Dict[str, Any] = {
            'response_format': response_format,
            'temperature': self.temperature if temperature is None else temperature,
            'max_tokens': max_tokens,
            'timeout': timeout,
        }
        if self._seed_kwarg:
            kwargs['seed'] = self.seed
        return self.inner.complete(messages=messages, **kwargs)

    @staticmethod
    def _accepts_seed_kwarg(client: LLMClient) -> bool:
        try:
            signature = inspect.signature(client.complete)
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            return False
        for parameter in signature.parameters.values():
            if parameter.name == 'seed':
                return True
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                return True
        return False


def proposers_from_clients(
    clients: Sequence[LLMClient],
    *,
    fallback: Optional[Attributor] = None,
    id_prefix: str = 'proposer',
    **attributor_kwargs: Any,
) -> List[ProposerSpec]:
    """One :class:`AllAtOnceAttributor` proposer per client, ids assigned in order.

    ``fallback`` defaults to :data:`NO_FALLBACK` on purpose. Under the usual
    default a model error turns into a silent heuristic answer, and inside a
    panel that is worse than a dropout: the heuristic result would be counted as
    a vote from a model that never answered. Fail-closed proposers show up as
    :data:`STATUS_UNAVAILABLE` instead.
    """

    specs: List[ProposerSpec] = []
    for index, client in enumerate(clients):
        model = str(getattr(client, 'model', '') or '')
        seed = getattr(client, 'seed', None)
        specs.append(
            ProposerSpec(
                proposer_id=f'{id_prefix}_{index:02d}',
                attributor=AllAtOnceAttributor(
                    client,
                    fallback=fallback if fallback is not None else NO_FALLBACK,
                    **attributor_kwargs,
                ),
                model=model,
                seed=seed if isinstance(seed, int) else None,
                seed_applied=bool(getattr(client, 'seed_applied', False)),
            )
        )
    return specs


def proposers_from_seeds(
    client: LLMClient,
    seeds: Sequence[int],
    *,
    temperature: float = 1.0,
    fallback: Optional[Attributor] = None,
    id_prefix: str = 'seed',
    **attributor_kwargs: Any,
) -> List[ProposerSpec]:
    """The same model N times, one seed per proposer.

    Each proposer gets its own :class:`SeededClient`, so the seeds are bound
    per proposer rather than mutated onto a shared client between calls. Ids are
    ``<id_prefix>_<seed>``, which keeps the deterministic tie-break readable.
    """

    specs: List[ProposerSpec] = []
    for seed in seeds:
        seeded = SeededClient(client, seed=seed, temperature=temperature)
        specs.append(
            ProposerSpec(
                proposer_id=f'{id_prefix}_{seed}',
                attributor=AllAtOnceAttributor(
                    seeded,
                    fallback=fallback if fallback is not None else NO_FALLBACK,
                    **attributor_kwargs,
                ),
                model=seeded.model,
                seed=seed,
                seed_applied=seeded.seed_applied,
            )
        )
    return specs


_MOA_SUMMARY_SYSTEM_PROMPT = """You are summarizing several independent diagnoses of ONE failed agent run.

Each diagnosis was written by a different analyst who could not see the others.
Your job is to produce the single best-supported diagnosis, not to average them.

Respond ONLY with a JSON object matching this schema (no prose, no markdown):

{
  "span_id": "<event_id from the input or null>",
  "step_index": <int or null>,
  "agent_name": "<agent_name from the input or null>",
  "confidence": <float between 0 and 1>,
  "rationale": "<one or two sentences justifying the choice>",
  "evidence": ["<short quoted evidence>", ...]
}

Rules:
1. Judge the diagnoses against the conversation, not against each other. How
   many analysts picked a step is NOT evidence that the step is right; several
   analysts can repeat the same mistake.
2. You may choose a step no analyst chose, if the conversation supports it
   better. Say so in the rationale when you do.
3. Quote evidence from the conversation itself, not from an analyst's wording.
4. "confidence" is your confidence in the diagnosis. Do not set it from the
   size of the agreement.
5. If the conversation does not appear to have failed, return all fields as
   null and confidence 0.
"""


class MixtureOfAgentsAttributor:
    """N independent proposers, then one summarizing agent over their proposals.

    Stage 1 runs each :class:`ProposerSpec` on the same trajectory and findings.
    Proposers never receive another proposer's output: each call is built from
    the trajectory alone, and they are run one at a time against separate
    attributor instances. Stage 2 sorts the proposals with
    :func:`proposal_sort_key` and sends them, once, to ``summarizer`` for a
    single diagnosis in the ordinary :class:`Blame` schema.

    The summarizer is a model call, deliberately, not arithmetic over the
    proposals. A vote can only return a step somebody named; a summarizer can
    weigh two incompatible readings, and can name a step none of the proposers
    named. Whether it did is recorded in
    :class:`SummaryProvenance`.

    NOT A CORRECTNESS CLAIM. Neither proposer agreement nor the summary is a
    label. Nothing in this class checks a diagnosis against the environment, a
    gold annotation, or a rerun; unanimous proposers can be unanimously wrong,
    and are likeliest to be when they share a model family or a prompt. The
    provenance exists so a reader can audit the hypothesis, not so the summary
    can be trusted without one.

    Usage:

    >>> panel = MixtureOfAgentsAttributor(  # doctest: +SKIP
    ...     proposers=proposers_from_clients([gpt, claude, gemini]),
    ...     summarizer=gpt,
    ... )
    >>> result = panel.attribute(trajectory, findings)  # doctest: +SKIP
    >>> result.raw['mixture_of_agents']['provenance']['matched_proposer_ids']  # doctest: +SKIP
    ['proposer_00', 'proposer_02']
    """

    id = 'mixture_of_agents'

    #: Proposers read the trajectory directly; findings are forwarded when the
    #: caller has them, but the panel does not require any.
    requires_findings: bool = False

    def __init__(
        self,
        proposers: Sequence[ProposerSpec],
        summarizer: LLMClient,
        *,
        fallback: Optional[Attributor] = None,
        max_tokens: int = 16000,
        summarizer_temperature: float = 0.0,
        include_trajectory: bool = True,
        max_rationale_chars: int = 600,
        max_evidence_items: int = 4,
        max_field_chars: int = 300,
    ) -> None:
        if not proposers:
            raise ValueError('MixtureOfAgentsAttributor requires at least one proposer')
        duplicates = [
            proposer_id
            for proposer_id, count in Counter(p.proposer_id for p in proposers).items()
            if count > 1
        ]
        if duplicates:
            raise ValueError(
                'proposer_id must be unique so provenance stays readable and the '
                f'ordering stays total; duplicated: {sorted(duplicates)}'
            )
        self.proposers = list(proposers)
        self.summarizer = summarizer
        self.fallback: Attributor = fallback or HeuristicAttributor()
        self.max_tokens = max_tokens
        self.summarizer_temperature = summarizer_temperature
        #: Whether the summarizer sees the conversation as well as the proposals.
        #: On by default: a summarizer without the trace can only referee wording.
        self.include_trajectory = include_trajectory
        self.max_rationale_chars = max_rationale_chars
        self.max_evidence_items = max_evidence_items
        self.max_field_chars = max_field_chars

    # -- stage 1 ----------------------------------------------------------

    def propose(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> List[Proposal]:
        """Run every proposer on the same input and record what each returned.

        Never raises on a proposer's behalf. A proposer that errors, declines or
        returns nothing becomes a :class:`Proposal` with the matching status, so
        a panel of N degrades to a panel of N-1 rather than to a traceback.
        """

        proposals: List[Proposal] = []
        for spec in self.proposers:
            proposals.append(self._run_one(spec, trajectory, findings))
        return sorted(proposals, key=proposal_sort_key)

    def _run_one(
        self,
        spec: ProposerSpec,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]],
    ) -> Proposal:
        started = time.perf_counter()
        method = str(getattr(spec.attributor, 'id', '') or '')
        try:
            result = spec.attributor.attribute(trajectory, findings)
        except AttributionUnavailable as exc:
            LOG.info('proposer %s declined: %s', spec.proposer_id, exc)
            return self._failed_proposal(
                spec, method, STATUS_UNAVAILABLE, str(exc), started,
            )
        except Exception as exc:  # one proposer must not end the panel
            LOG.warning('proposer %s raised: %s', spec.proposer_id, exc)
            return self._failed_proposal(spec, method, STATUS_ERROR, str(exc), started)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not result.hypotheses:
            return Proposal(
                proposer_id=spec.proposer_id,
                model=spec.model,
                seed=spec.seed,
                status=STATUS_EMPTY,
                method=result.method or method,
                elapsed_ms=elapsed_ms,
                error='attributor returned no hypotheses',
                usage=result.usage,
            )
        return Proposal(
            proposer_id=spec.proposer_id,
            model=spec.model,
            seed=spec.seed,
            status=STATUS_OK,
            method=result.method or method,
            blame=result.hypotheses[0],
            elapsed_ms=elapsed_ms,
            usage=result.usage,
        )

    @staticmethod
    def _failed_proposal(
        spec: ProposerSpec,
        method: str,
        status: str,
        error: str,
        started: float,
    ) -> Proposal:
        return Proposal(
            proposer_id=spec.proposer_id,
            model=spec.model,
            seed=spec.seed,
            status=status,
            method=method,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )

    # -- stage 2 ----------------------------------------------------------

    def analyze(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> MixtureOfAgentsDiagnosis:
        """Run both stages and return the typed result with its audit trail."""

        started = time.perf_counter()
        proposals = self.propose(trajectory, findings)
        usable = [p for p in proposals if p.status == STATUS_OK and p.blame is not None]
        usage = TokenUsage()
        for proposal in proposals:
            usage = usage + proposal.usage

        if not usable:
            provenance = self._provenance(
                summary_source=SUMMARY_FALLBACK,
                summary=None,
                proposals=proposals,
                usable=usable,
                summarizer_error='no proposer produced a hypothesis',
            )
            return MixtureOfAgentsDiagnosis(
                proposals=proposals,
                summary=None,
                provenance=provenance,
                usage=usage,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

        summary, summarizer_error, summarizer_usage = self._summarize(
            trajectory, usable,
        )
        usage = usage + summarizer_usage
        if summary is None:
            summary = self._promote_top_proposal(usable)
            source = SUMMARY_DEGRADED
        else:
            source = SUMMARY_FROM_SUMMARIZER
        provenance = self._provenance(
            summary_source=source,
            summary=summary,
            proposals=proposals,
            usable=usable,
            summarizer_error=summarizer_error,
        )
        if not provenance.agrees_with_any_proposer:
            LOG.info(
                'mixture_of_agents summary blames step %s, which no proposer named',
                summary.step_index,
            )
        return MixtureOfAgentsDiagnosis(
            proposals=proposals,
            summary=summary,
            provenance=provenance,
            usage=usage,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def _summarize(
        self,
        trajectory: AgentTrajectory,
        usable: List[Proposal],
    ) -> Tuple[Optional[Blame], str, TokenUsage]:
        """One summarizer call. Returns (summary, error, usage); summary is None on failure."""

        messages = [
            {'role': 'system', 'content': _MOA_SUMMARY_SYSTEM_PROMPT},
            {'role': 'user', 'content': self.render_summary_prompt(trajectory, usable)},
        ]
        try:
            completion = self.summarizer.complete(
                messages=messages,
                temperature=self.summarizer_temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # degrade, and record that we did
            LOG.warning('mixture_of_agents summarizer failed: %s', exc)
            return None, f'summarizer call failed: {exc}', TokenUsage()
        reported = getattr(completion, 'usage', None)
        usage = reported if isinstance(reported, TokenUsage) else TokenUsage()
        parsed = extract_json_block(completion.text or '')
        if not parsed:
            LOG.info('mixture_of_agents summarizer returned no JSON block')
            return None, 'summarizer returned no JSON block', usage
        blame = Blame(
            span_id=AllAtOnceAttributor._coerce_str(parsed.get('span_id')),
            step_index=AllAtOnceAttributor._coerce_int(parsed.get('step_index')),
            agent_name=AllAtOnceAttributor._coerce_str(parsed.get('agent_name')),
            confidence=AllAtOnceAttributor._coerce_float(
                parsed.get('confidence'), default=0.0,
            ),
            rationale=str(parsed.get('rationale') or ''),
            evidence=AllAtOnceAttributor._coerce_str_list(parsed.get('evidence')),
            sources=[self.id],
        )
        return AllAtOnceAttributor._normalize_blame(trajectory, blame), '', usage

    def render_summary_prompt(
        self,
        trajectory: AgentTrajectory,
        usable: List[Proposal],
    ) -> str:
        """The exact text sent to the summarizer. Deterministic for a fixed panel.

        Exposed because a prompt that cannot be read cannot be audited, and
        because a test asserting the ordering should assert on the thing that is
        actually sent rather than on a reconstruction of it.
        """

        blocks: List[str] = [
            'A multi-step agent run FAILED.',
            f'The goal was: {trajectory.goal or "(unknown goal)"}',
        ]
        if self.include_trajectory:
            blocks.append(f'\nHere is the conversation:\n\n{self._render_events(trajectory)}')
        blocks.append(
            f'\nHere are {len(usable)} independent diagnoses of that run. '
            'They were written without sight of each other, and they are listed '
            'in a fixed order that carries no ranking.'
        )
        for position, proposal in enumerate(usable, start=1):
            blocks.append(self._render_proposal(position, proposal))
        blocks.append(
            '\nProduce ONE diagnosis of this run as the JSON object described '
            'above. The number of analysts naming a step is not evidence about '
            'the step.'
        )
        return '\n'.join(blocks)

    def _render_proposal(self, position: int, proposal: Proposal) -> str:
        blame = proposal.blame
        rationale = (blame.rationale if blame else '')[: self.max_rationale_chars]
        evidence = list(blame.evidence)[: self.max_evidence_items] if blame else []
        evidence_doc = (
            '\n'.join(f'  - {item[: self.max_field_chars]}' for item in evidence)
            or '  (none quoted)'
        )
        return (
            f'\n## Diagnosis {position}\n'
            f'step_index: {proposal.step_index}\n'
            f'event_id: {proposal.span_id}\n'
            f'agent_name: {blame.agent_name if blame else None}\n'
            f'confidence: {proposal.confidence:.2f}\n'
            f'rationale: {rationale}\n'
            f'evidence:\n{evidence_doc}'
        )

    def _render_events(self, trajectory: AgentTrajectory) -> str:
        """The conversation, in the same line shape the all-at-once prompt uses."""

        lines: List[str] = []
        for event in trajectory.events:
            body = str(event.output) if event.output is not None else str(event.input or '')
            body = body[: self.max_field_chars]
            step = event.step_index if event.step_index is not None else '?'
            error = f' | ERROR: {str(event.error)[: self.max_field_chars]}' if event.error else ''
            lines.append(f'Step {step} [{event.event_id}] {event.agent_name}: {body}{error}')
        return '\n'.join(lines) or '(empty conversation)'

    # -- provenance -------------------------------------------------------

    @staticmethod
    def _promote_top_proposal(usable: List[Proposal]) -> Blame:
        """Most-supported proposal, deterministically, when the summarizer failed.

        Ranked by how many proposers named the same step, then by
        :func:`proposal_sort_key`. This is a vote, which is exactly what this
        engine exists not to be, so the caller is told: the provenance records
        :data:`SUMMARY_DEGRADED` and never :data:`SUMMARY_FROM_SUMMARIZER`.
        """

        votes: 'Counter[int]' = Counter(
            p.step_index for p in usable if p.step_index is not None
        )

        def rank(proposal: Proposal) -> Tuple[int, Tuple[int, int, float, str]]:
            step = proposal.step_index
            support = -votes[step] if step is not None else 1
            return (support, proposal_sort_key(proposal))

        winner = sorted(usable, key=rank)[0]
        blame = winner.blame
        if blame is None:  # unreachable: callers pass usable proposals only
            raise ValueError('promoted proposal carries no blame')
        return Blame(
            span_id=blame.span_id,
            step_index=blame.step_index,
            agent_name=blame.agent_name,
            confidence=blame.confidence,
            rationale=(
                f'[no summary: promoted proposal from {winner.proposer_id}] '
                f'{blame.rationale}'
            ),
            evidence=list(blame.evidence),
            sources=['mixture_of_agents', winner.proposer_id],
            corrected_action=blame.corrected_action,
        )

    def _provenance(
        self,
        *,
        summary_source: str,
        summary: Optional[Blame],
        proposals: List[Proposal],
        usable: List[Proposal],
        summarizer_error: str,
    ) -> SummaryProvenance:
        steps = [p.step_index for p in usable if p.step_index is not None]
        step_votes = {str(step): count for step, count in sorted(Counter(steps).items())}
        matched: List[str] = []
        span_matched: List[str] = []
        if summary is not None:
            for proposal in usable:
                if summary.step_index is not None and proposal.step_index == summary.step_index:
                    matched.append(proposal.proposer_id)
                if summary.span_id is not None and proposal.span_id == summary.span_id:
                    span_matched.append(proposal.proposer_id)
        return SummaryProvenance(
            summary_source=summary_source,
            summarizer_model=str(getattr(self.summarizer, 'model', '') or ''),
            matched_proposer_ids=sorted(matched),
            span_matched_proposer_ids=sorted(span_matched),
            agrees_with_any_proposer=bool(matched or span_matched),
            proposers_unanimous=(
                len(usable) > 1
                and len(steps) == len(usable)
                and len(set(steps)) == 1
            ),
            step_votes=step_votes,
            proposal_order=[p.proposer_id for p in usable],
            proposers_run=len(proposals),
            proposers_usable=len(usable),
            summarizer_error=summarizer_error,
        )

    # -- Attributor protocol ----------------------------------------------

    def attribute(
        self,
        trajectory: AgentTrajectory,
        findings: Optional[List[FailureFinding]] = None,
    ) -> AttributionResult:
        """Run the panel and return the summary as an ordinary attribution result.

        ``hypotheses[0]`` is the summary. The proposals follow it, in the order
        the summarizer saw them, so a consumer that only reads hypotheses still
        sees every reading rather than only the summarized one. The full audit
        trail is under ``raw['mixture_of_agents']``.
        """

        findings = findings or []
        diagnosis = self.analyze(trajectory, findings)
        if diagnosis.summary is None:
            fallback_result = self.fallback.attribute(trajectory, findings)
            fallback_result.raw = {
                **fallback_result.raw,
                'mixture_of_agents': diagnosis.as_raw_payload(),
                'method_requested': self.id,
            }
            return fallback_result
        hypotheses = [diagnosis.summary] + [
            proposal.blame
            for proposal in diagnosis.usable_proposals()
            if proposal.blame is not None
        ]
        return AttributionResult(
            method=self.id,
            hypotheses=hypotheses,
            elapsed_ms=diagnosis.elapsed_ms,
            raw={'mixture_of_agents': diagnosis.as_raw_payload()},
            usage=diagnosis.usage,
        )


__all__ = [
    'MixtureOfAgentsAttributor',
    'MixtureOfAgentsDiagnosis',
    'Proposal',
    'ProposerSpec',
    'SeededClient',
    'SummaryProvenance',
    'SUMMARY_DEGRADED',
    'SUMMARY_FALLBACK',
    'SUMMARY_FROM_SUMMARIZER',
    'STATUS_EMPTY',
    'STATUS_ERROR',
    'STATUS_OK',
    'STATUS_UNAVAILABLE',
    'proposal_sort_key',
    'proposers_from_clients',
    'proposers_from_seeds',
]
