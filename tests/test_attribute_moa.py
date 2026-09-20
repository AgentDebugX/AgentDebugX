"""Mixture-of-agents attribution: N proposers, one summarizer, full provenance.

Every model call here is a stub. Nothing in this file touches the network.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from agentdebug.diagnose.attribute import (
    NO_FALLBACK,
    AllAtOnceAttributor,
    AttributionResult,
    Blame,
    HeuristicAttributor,
    MixtureOfAgentsAttributor,
    ProposerSpec,
    SeededClient,
    proposal_sort_key,
    proposers_from_clients,
    proposers_from_seeds,
)
from agentdebug.diagnose.attribute.moa import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    SUMMARY_DEGRADED,
    SUMMARY_FALLBACK,
    SUMMARY_FROM_SUMMARIZER,
    Proposal,
)
from agentdebug.diagnose.registry import get_component_metadata, load_component
from agentdebug.runtime.llm import CompletionResult, TokenUsage
from agentdebug.schema import AgentTrajectory


def _blame_json(step: int, *, agent: str = 'planner', confidence: float = 0.8,
                rationale: str = 'decisive mistake', span_id: Optional[str] = None) -> str:
    return json.dumps({
        'span_id': span_id,
        'step_index': step,
        'agent_name': agent,
        'confidence': confidence,
        'rationale': rationale,
        'evidence': [f'step {step} evidence'],
    })


class ScriptedLLM:
    """Returns canned replies in order and records every prompt it received."""

    def __init__(self, replies: List[str], *, model: str = 'stub-model',
                 usage: Optional[TokenUsage] = None) -> None:
        self.model = model
        self._replies = list(replies)
        self._usage = usage or TokenUsage()
        self.calls: List[List[Dict[str, Any]]] = []
        self.kwargs: List[Dict[str, Any]] = []

    def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        reply = self._replies.pop(0) if self._replies else self._replies_exhausted()
        return CompletionResult(text=reply, raw={}, usage=self._usage)

    @staticmethod
    def _replies_exhausted() -> str:
        raise AssertionError('ScriptedLLM ran out of scripted replies')

    @property
    def user_prompt(self) -> str:
        return str(self.calls[-1][-1]['content'])


class ExplodingLLM:
    """Every call raises, the way an unreachable gateway does."""

    model = 'exploding-model'

    def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
        raise RuntimeError('gateway unreachable')


class RaisingAttributor:
    """A proposer whose own code fails, not just its model."""

    id = 'raising'
    requires_findings = False

    def attribute(self, trajectory: AgentTrajectory, findings: Any = None) -> AttributionResult:
        raise ValueError('proposer exploded')


class EmptyAttributor:
    """A proposer that runs to completion and blames nothing."""

    id = 'empty'
    requires_findings = False

    def attribute(self, trajectory: AgentTrajectory, findings: Any = None) -> AttributionResult:
        return AttributionResult(method=self.id, hypotheses=[])


def _proposer(proposer_id: str, replies: List[str], *, model: str = 'stub-model') -> ProposerSpec:
    llm = ScriptedLLM(replies, model=model)
    return ProposerSpec(
        proposer_id=proposer_id,
        attributor=AllAtOnceAttributor(llm, fallback=NO_FALLBACK),
        model=model,
    )


# --------------------------------------------------------------------------
# Agreement
# --------------------------------------------------------------------------


def test_agreeing_proposers_produce_a_summary_with_full_provenance(failed_trajectory) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(1)], model='model-a'),
            _proposer('b', [_blame_json(1)], model='model-b'),
            _proposer('c', [_blame_json(1)], model='model-c'),
        ],
        summarizer=ScriptedLLM([_blame_json(1, rationale='all three agree on the plan step')]),
    )
    diagnosis = panel.analyze(failed_trajectory)

    assert [p.status for p in diagnosis.proposals] == [STATUS_OK] * 3
    assert diagnosis.summary is not None
    assert diagnosis.summary.step_index == 1
    provenance = diagnosis.provenance
    assert provenance.summary_source == SUMMARY_FROM_SUMMARIZER
    assert provenance.matched_proposer_ids == ['a', 'b', 'c']
    assert provenance.agrees_with_any_proposer is True
    assert provenance.proposers_unanimous is True
    assert provenance.step_votes == {'1': 3}
    assert provenance.proposers_run == 3
    assert provenance.proposers_usable == 3


def test_every_proposal_keeps_its_own_model_and_rationale(failed_trajectory) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(1, rationale='the planner dropped the constraint')],
                      model='model-a'),
            _proposer('b', [_blame_json(2, rationale='the browser call was malformed')],
                      model='model-b'),
        ],
        summarizer=ScriptedLLM([_blame_json(1)]),
    )
    payload = panel.attribute(failed_trajectory).raw['mixture_of_agents']

    by_id = {row['proposer_id']: row for row in payload['proposals']}
    assert by_id['a']['model'] == 'model-a'
    assert by_id['b']['model'] == 'model-b'
    assert 'dropped the constraint' in by_id['a']['rationale']
    assert 'malformed' in by_id['b']['rationale']
    assert by_id['a']['method'] == 'all_at_once'
    assert payload['disclaimer'].startswith('Proposer agreement is not a correctness label')


# --------------------------------------------------------------------------
# Disagreement, including a summary nobody proposed
# --------------------------------------------------------------------------


def test_disagreeing_proposers_are_counted_not_collapsed(failed_trajectory) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(1)]),
            _proposer('b', [_blame_json(2, agent='browser')]),
            _proposer('c', [_blame_json(2, agent='browser')]),
        ],
        summarizer=ScriptedLLM([_blame_json(2, agent='browser')]),
    )
    diagnosis = panel.analyze(failed_trajectory)

    assert diagnosis.provenance.step_votes == {'1': 1, '2': 2}
    assert diagnosis.provenance.proposers_unanimous is False
    assert diagnosis.provenance.matched_proposer_ids == ['b', 'c']
    assert diagnosis.provenance.agrees_with_any_proposer is True


def test_a_summary_no_proposer_supports_is_visible(failed_trajectory) -> None:
    """The summarizer may name a step nobody proposed. That must not be hidden."""

    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(2, agent='browser')]),
            _proposer('b', [_blame_json(2, agent='browser')]),
        ],
        summarizer=ScriptedLLM([_blame_json(1, rationale='both analysts blamed the symptom')]),
    )
    diagnosis = panel.analyze(failed_trajectory)

    assert diagnosis.summary is not None
    assert diagnosis.summary.step_index == 1
    assert diagnosis.provenance.matched_proposer_ids == []
    assert diagnosis.provenance.span_matched_proposer_ids == []
    assert diagnosis.provenance.agrees_with_any_proposer is False
    # The proposals survive the disagreement, so the reader can check both.
    assert [p.step_index for p in diagnosis.usable_proposals()] == [2, 2]


def test_summary_is_a_separate_call_not_a_vote(failed_trajectory) -> None:
    """Two proposers say step 2, the summarizer says step 1, and the summary wins."""

    summarizer = ScriptedLLM([_blame_json(1)])
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(2, agent='browser')]),
            _proposer('b', [_blame_json(2, agent='browser')]),
        ],
        summarizer=summarizer,
    )
    result = panel.attribute(failed_trajectory)

    assert len(summarizer.calls) == 1
    assert result.hypotheses[0].step_index == 1
    assert result.hypotheses[0].sources == ['mixture_of_agents']
    # Proposals follow the summary rather than being dropped.
    assert [h.step_index for h in result.hypotheses[1:]] == [2, 2]


# --------------------------------------------------------------------------
# Independence and deterministic ordering
# --------------------------------------------------------------------------


def test_proposers_never_see_each_other(failed_trajectory) -> None:
    first = ScriptedLLM([_blame_json(1, rationale='RATIONALE_FROM_A')])
    second = ScriptedLLM([_blame_json(2, rationale='RATIONALE_FROM_B')])
    panel = MixtureOfAgentsAttributor(
        proposers=[
            ProposerSpec('a', AllAtOnceAttributor(first, fallback=NO_FALLBACK)),
            ProposerSpec('b', AllAtOnceAttributor(second, fallback=NO_FALLBACK)),
        ],
        summarizer=ScriptedLLM([_blame_json(1)]),
    )
    panel.analyze(failed_trajectory)

    assert len(first.calls) == 1 and len(second.calls) == 1
    assert 'RATIONALE_FROM_B' not in first.user_prompt
    assert 'RATIONALE_FROM_A' not in second.user_prompt
    # And neither proposer was told it was part of a panel at all.
    assert 'Diagnosis 1' not in first.user_prompt


def test_summarizer_prompt_does_not_depend_on_declaration_order(failed_trajectory) -> None:
    def build(order: List[str]) -> str:
        specs = {
            'a': _proposer('a', [_blame_json(2, agent='browser', confidence=0.4)]),
            'b': _proposer('b', [_blame_json(1, confidence=0.9)]),
            'c': _proposer('c', [_blame_json(1, confidence=0.6)]),
        }
        summarizer = ScriptedLLM([_blame_json(1)])
        MixtureOfAgentsAttributor(
            proposers=[specs[name] for name in order],
            summarizer=summarizer,
        ).analyze(failed_trajectory)
        return summarizer.user_prompt

    prompt = build(['a', 'b', 'c'])
    assert prompt == build(['c', 'a', 'b'])
    # And the fixed order is the documented one: step, then confidence, then id.
    assert prompt.index('confidence: 0.90') < prompt.index('confidence: 0.60')
    assert prompt.index('confidence: 0.60') < prompt.index('confidence: 0.40')
    assert prompt.count('## Diagnosis ') == 3
    assert 'not evidence about the step' in prompt


def test_proposal_sort_key_orders_by_step_then_confidence_then_id() -> None:
    def proposal(proposer_id: str, step: Optional[int], confidence: float) -> Proposal:
        blame = (
            Blame(span_id=None, step_index=step, agent_name=None,
                  confidence=confidence, rationale='')
            if step is not None or confidence
            else None
        )
        return Proposal(proposer_id=proposer_id, model='m', seed=None,
                        status=STATUS_OK, blame=blame)

    unlocalized = Proposal(
        proposer_id='z', model='m', seed=None, status=STATUS_OK,
        blame=Blame(span_id=None, step_index=None, agent_name=None,
                    confidence=0.9, rationale=''),
    )
    rows = [
        proposal('b', 3, 0.2),
        unlocalized,
        proposal('a', 1, 0.5),
        proposal('c', 1, 0.9),
    ]
    assert [p.proposer_id for p in sorted(rows, key=proposal_sort_key)] == ['c', 'a', 'b', 'z']


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


def test_one_failing_and_one_malformed_proposer_do_not_stop_the_panel(
    failed_trajectory,
) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('good', [_blame_json(1)]),
            # No JSON anywhere in the reply, and fail-closed, so it declines.
            _proposer('malformed', ['I think the agent was confused, honestly.']),
            ProposerSpec('raiser', RaisingAttributor()),
            ProposerSpec('blank', EmptyAttributor()),
            ProposerSpec('dead', AllAtOnceAttributor(ExplodingLLM(), fallback=NO_FALLBACK)),
        ],
        summarizer=ScriptedLLM([_blame_json(1)]),
    )
    diagnosis = panel.analyze(failed_trajectory)

    statuses = {p.proposer_id: p.status for p in diagnosis.proposals}
    assert statuses['good'] == STATUS_OK
    assert statuses['malformed'] == STATUS_UNAVAILABLE
    assert statuses['raiser'] == STATUS_ERROR
    assert statuses['blank'] == STATUS_EMPTY
    assert statuses['dead'] == STATUS_UNAVAILABLE
    assert diagnosis.provenance.proposers_run == 5
    assert diagnosis.provenance.proposers_usable == 1
    assert diagnosis.summary is not None
    assert diagnosis.provenance.summary_source == SUMMARY_FROM_SUMMARIZER
    errors = {p.proposer_id: p.error for p in diagnosis.proposals}
    assert 'proposer exploded' in errors['raiser']
    assert errors['blank']


def test_a_failing_summarizer_degrades_to_the_most_supported_proposal(
    failed_trajectory,
) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            _proposer('a', [_blame_json(1)]),
            _proposer('b', [_blame_json(2, agent='browser')]),
            _proposer('c', [_blame_json(2, agent='browser')]),
        ],
        summarizer=ExplodingLLM(),
    )
    diagnosis = panel.analyze(failed_trajectory)

    assert diagnosis.summary is not None
    assert diagnosis.summary.step_index == 2  # the two-vote step, not the one-vote step
    assert diagnosis.provenance.summary_source == SUMMARY_DEGRADED
    assert 'gateway unreachable' in diagnosis.provenance.summarizer_error
    assert 'no summary' in diagnosis.summary.rationale
    assert 'mixture_of_agents' in diagnosis.summary.sources


def test_a_summarizer_without_json_degrades_and_says_so(failed_trajectory) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[_proposer('a', [_blame_json(1)])],
        summarizer=ScriptedLLM(['Step 1 looks wrong but I will not format it.']),
    )
    diagnosis = panel.analyze(failed_trajectory)

    assert diagnosis.provenance.summary_source == SUMMARY_DEGRADED
    assert diagnosis.provenance.summarizer_error == 'summarizer returned no JSON block'


def test_no_usable_proposal_falls_back_and_records_why(failed_trajectory,
                                                       diagnostic_report) -> None:
    panel = MixtureOfAgentsAttributor(
        proposers=[
            ProposerSpec('raiser', RaisingAttributor()),
            ProposerSpec('blank', EmptyAttributor()),
        ],
        summarizer=ScriptedLLM([]),
        fallback=HeuristicAttributor(),
    )
    result = panel.attribute(failed_trajectory, diagnostic_report.findings)

    assert result.method == 'heuristic'
    assert result.raw['method_requested'] == 'mixture_of_agents'
    payload = result.raw['mixture_of_agents']
    assert payload['provenance']['summary_source'] == SUMMARY_FALLBACK
    assert payload['provenance']['proposers_usable'] == 0
    assert len(payload['proposals']) == 2


# --------------------------------------------------------------------------
# Seeds: the same model N times
# --------------------------------------------------------------------------


def test_same_model_repeated_with_different_seeds(failed_trajectory) -> None:
    class ExtraBodyLLM:
        model = 'one-model'

        def __init__(self) -> None:
            self.extra_body: Dict[str, Any] = {'provider': 'hub'}
            self.seen: List[Dict[str, Any]] = []

        def complete(self, messages: List[Dict[str, Any]], *,
                     response_format: Any = None, temperature: float = 0.0,
                     max_tokens: int = 2048, timeout: float = 60.0) -> CompletionResult:
            self.seen.append({'extra_body': dict(self.extra_body), 'temperature': temperature})
            return CompletionResult(text=_blame_json(1), raw={})

    client = ExtraBodyLLM()
    specs = proposers_from_seeds(client, [7, 11, 13], temperature=0.8)

    assert [spec.proposer_id for spec in specs] == ['seed_7', 'seed_11', 'seed_13']
    assert [spec.seed for spec in specs] == [7, 11, 13]
    assert all(spec.seed_applied for spec in specs)
    assert all(spec.model == 'one-model' for spec in specs)
    # The caller's own client is never mutated, and each proposer carries its own copy.
    assert client.extra_body == {'provider': 'hub'}

    panel = MixtureOfAgentsAttributor(
        proposers=specs, summarizer=ScriptedLLM([_blame_json(1)]),
    )
    payload = panel.attribute(failed_trajectory).raw['mixture_of_agents']
    assert [row['seed'] for row in payload['proposals']] == [11, 13, 7]  # sorted by id
    assert {row['model'] for row in payload['proposals']} == {'one-model'}


def test_seeded_client_prefers_a_seed_keyword_when_the_client_takes_one() -> None:
    class SeedKwargLLM:
        model = 'kwarg-model'

        def __init__(self) -> None:
            self.seen: List[Dict[str, Any]] = []

        def complete(self, messages: List[Dict[str, Any]], *, seed: Optional[int] = None,
                     temperature: float = 0.0, **kwargs: Any) -> CompletionResult:
            self.seen.append({'seed': seed, 'temperature': temperature})
            return CompletionResult(text='{}', raw={})

    inner = SeedKwargLLM()
    client = SeededClient(inner, seed=5, temperature=0.7)
    assert client.seed_applied is True
    client.complete([{'role': 'user', 'content': 'hi'}])
    assert inner.seen == [{'seed': 5, 'temperature': 0.7}]


def test_a_client_that_cannot_carry_a_seed_reports_seed_applied_false() -> None:
    class RigidLLM:
        model = 'rigid-model'

        def complete(self, messages: List[Dict[str, Any]], *, response_format: Any = None,
                     temperature: float = 0.0, max_tokens: int = 2048,
                     timeout: float = 60.0) -> CompletionResult:
            return CompletionResult(text='{}', raw={})

    client = SeededClient(RigidLLM(), seed=5)
    assert client.seed_applied is False
    assert client.model == 'rigid-model'


# --------------------------------------------------------------------------
# Wiring and guardrails
# --------------------------------------------------------------------------


def test_proposers_from_clients_fails_closed_by_default() -> None:
    specs = proposers_from_clients([ScriptedLLM([], model='m1'), ScriptedLLM([], model='m2')])

    assert [spec.proposer_id for spec in specs] == ['proposer_00', 'proposer_01']
    assert [spec.model for spec in specs] == ['m1', 'm2']
    for spec in specs:
        assert spec.attributor.fallback is NO_FALLBACK


def test_usage_is_summed_across_proposers_and_the_summarizer(failed_trajectory) -> None:
    summarizer = ScriptedLLM(
        [_blame_json(1)],
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20, calls=1),
    )
    panel = MixtureOfAgentsAttributor(
        proposers=[_proposer('a', [_blame_json(1)])], summarizer=summarizer,
    )
    result = panel.attribute(failed_trajectory)

    assert result.usage.prompt_tokens == 100
    assert result.usage.calls == 1


def test_empty_and_duplicate_panels_are_rejected() -> None:
    with pytest.raises(ValueError, match='at least one proposer'):
        MixtureOfAgentsAttributor(proposers=[], summarizer=ScriptedLLM([]))

    with pytest.raises(ValueError, match='unique'):
        MixtureOfAgentsAttributor(
            proposers=[
                ProposerSpec('same', RaisingAttributor()),
                ProposerSpec('same', RaisingAttributor()),
            ],
            summarizer=ScriptedLLM([]),
        )


def test_the_component_is_registered_in_the_diagnose_registry() -> None:
    metadata = get_component_metadata('attribute.mixture_of_agents')

    assert metadata.stage == 'attribute'
    assert metadata.enabled_by_default is False
    assert load_component('attribute.mixture_of_agents') is MixtureOfAgentsAttributor
