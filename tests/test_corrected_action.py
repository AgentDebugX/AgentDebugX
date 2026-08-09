"""Attributors emitting a concrete corrected action for the blamed step.

An attribution rationale explains the past. A harness that wants to prove the localization
was right has to RE-RUN the trajectory with exactly one step replaced, and prose cannot be
substituted into a step -- so before this field every attributor in the library topped out
at "here is a paragraph about what went wrong", and the strongest form of evidence was
unreachable through the library however good the localization was.

The field is optional and must stay honest. These tests pin the three things that make it
honest rather than decorative:

  * default OFF -- prompts byte-identical, no extra call, no field;
  * null over a guess -- an unusable emission becomes None, not a coerced approximation;
  * absent, no-op and real are three DIFFERENT states, all readable by the consumer.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agentdebug.diagnose.attribute import (
    AllAtOnceAttributor,
    BinarySearchAttributor,
    Blame,
    CorrectedAction,
    CounterfactualAttributor,
    EnsembleAttributor,
    HeuristicAttributor,
    StepByStepAttributor,
)
from agentdebug.diagnose.attribute.attribution import (
    _ATTR_SYSTEM_PROMPT,
    _PROPOSE_ACTION_SYSTEM_PROMPT,
    _STEP_SYSTEM_PROMPT,
)
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, FailureFinding, FailureMode


class RecordingLLM:
    """Returns canned text and keeps every request, so prompts can be asserted on.

    `action_response` is dispatched by system prompt rather than by call index: the number
    of probes a bisect or a counterfactual sweep makes is an implementation detail, and a
    test that hard-codes it would break for reasons unrelated to what it is checking.
    """

    model = 'fake-attributor'

    def __init__(self, *responses: str, action_response: str | None = None) -> None:
        self._responses = list(responses)
        self._action_response = action_response
        self.calls: List[List[Dict[str, Any]]] = []

    def complete(self, messages: List[Dict[str, Any]], **kwargs: Any) -> CompletionResult:
        self.calls.append(messages)
        if self._action_response is not None and _is_action_request(messages):
            return CompletionResult(text=self._action_response, raw={})
        text = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return CompletionResult(text=text, raw={})

    @property
    def systems(self) -> List[str]:
        return [m[0]['content'] for m in self.calls]

    @property
    def action_requests(self) -> int:
        return sum(1 for m in self.calls if _is_action_request(m))


def _is_action_request(messages: List[Dict[str, Any]]) -> bool:
    return messages[0]['content'] == _PROPOSE_ACTION_SYSTEM_PROMPT


def acting_trajectory() -> AgentTrajectory:
    """A trace whose actions live in `tool.call` inputs -- the shape the field mirrors."""
    trajectory = AgentTrajectory(
        trace_id='trace_act',
        task_id='task_act',
        goal='Put the plate in the drawer.',
        framework='test-framework',
    )
    trajectory.add_event(
        AgentEvent(
            event_id='thought_2',
            trace_id=trajectory.trace_id,
            agent_name='solver',
            event_type='agent.step',
            step_index=2,
            output='I should take the plate.',
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='call_2',
            trace_id=trajectory.trace_id,
            agent_name='solver',
            event_type='tool.call',
            step_index=2,
            input={'tool': 'take', 'args': {'object': 'plate'}},
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='result_2',
            trace_id=trajectory.trace_id,
            agent_name='env',
            event_type='tool.result',
            step_index=2,
            output='Nothing happens.',
            error='object not present: plate',
        )
    )
    return trajectory


def blame_json(corrected: str) -> str:
    return (
        '{"span_id":"call_2","step_index":2,"agent_name":"solver",'
        '"confidence":0.8,"rationale":"took the wrong object",'
        f'"evidence":["object not present"],"corrected_action":{corrected}}}'
    )


# --- backwards compatibility -------------------------------------------------------------


def test_blame_constructs_without_the_new_field(failed_trajectory: AgentTrajectory) -> None:
    """Every pre-existing construction site passes no corrected_action. It must still work."""
    blame = Blame(
        span_id='evt_plan', step_index=1, agent_name='planner',
        confidence=0.5, rationale='because',
    )
    assert blame.corrected_action is None


def test_default_is_off_and_the_prompt_is_byte_identical() -> None:
    """Upgrading the library must not change what an existing caller's model is asked.

    A changed system prompt can move the attribution itself, so a caller that never asked
    for a corrected action must get the exact prompt it got before the feature existed.
    """
    trajectory = acting_trajectory()
    llm = RecordingLLM(blame_json('{"tool":"take","args":{"object":"plate 1"}}'))

    result = AllAtOnceAttributor(llm).attribute(trajectory)

    assert llm.systems == [_ATTR_SYSTEM_PROMPT]
    assert 'corrected_action' not in llm.systems[0]
    # ... and even though the model volunteered one, an attributor that was not asked does
    # not smuggle it into the result.
    assert result.hypotheses[0].corrected_action is None
    assert result.raw['corrected_action'] == {
        'requested': False, 'emitted': False, 'reason': 'not_requested',
        'source': 'all_at_once', 'differs_from_original': None,
    }


def test_step_by_step_default_prompt_is_byte_identical() -> None:
    trajectory = acting_trajectory()
    llm = RecordingLLM('{"is_failure_step":false,"confidence":0.1,"rationale":"fine"}')

    StepByStepAttributor(llm).attribute(trajectory)

    assert set(llm.systems) == {_STEP_SYSTEM_PROMPT}


def test_opting_in_costs_all_at_once_no_extra_call() -> None:
    """The field rides along in a JSON object the attributor already requests."""
    llm = RecordingLLM(blame_json('{"tool":"take","args":{"object":"plate 1"}}'))

    AllAtOnceAttributor(llm, propose_corrected_action=True).attribute(acting_trajectory())

    assert len(llm.calls) == 1
    assert 'corrected_action' in llm.systems[0]


# --- the three states --------------------------------------------------------------------


def test_a_real_correction_reports_that_it_differs() -> None:
    llm = RecordingLLM(blame_json('{"tool":"take","args":{"object":"plate 1"}}'))

    result = AllAtOnceAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    action = result.hypotheses[0].corrected_action
    assert action is not None
    assert action.tool == 'take'
    assert action.args == {'object': 'plate 1'}
    assert action.source == 'all_at_once'
    # Read off the trace, not off the model: this is the action the blamed step actually took.
    assert action.original == {'tool': 'take', 'args': {'object': 'plate'}}
    assert action.differs_from_original is True
    assert action.as_event_input() == {'tool': 'take', 'args': {'object': 'plate 1'}}
    assert result.raw['corrected_action']['reason'] == 'emitted'


def test_an_action_identical_to_the_original_is_flagged_not_hidden() -> None:
    """The case the consumer must be able to see coming.

    A model that echoes the step's own action produces a substitution that changes nothing,
    so a rerun of it proves nothing. That is NOT the same as declining, and the two must not
    collapse into the same value -- otherwise a no-op rerun gets counted as evidence.
    """
    llm = RecordingLLM(blame_json('{"tool":"take","args":{"object":"plate"}}'))

    result = AllAtOnceAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    action = result.hypotheses[0].corrected_action
    assert action is not None                       # emitted ...
    assert action.differs_from_original is False    # ... and provably a no-op
    assert result.raw['corrected_action']['emitted'] is True
    assert result.raw['corrected_action']['differs_from_original'] is False


def test_declining_yields_none_and_says_so() -> None:
    llm = RecordingLLM(blame_json('null'))

    result = AllAtOnceAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    assert result.hypotheses[0].corrected_action is None
    assert result.raw['corrected_action'] == {
        'requested': True, 'emitted': False, 'reason': 'declined_by_model',
        'source': 'all_at_once', 'differs_from_original': None,
    }


def test_unknowable_difference_is_none_not_false() -> None:
    """A blamed step with no action of its own cannot be compared, and we do not pretend.

    False would claim "the correction is a no-op". None says "there was nothing to differ
    from". Collapsing them would silently downgrade a genuine correction.
    """
    trajectory = AgentTrajectory(trace_id='no_action', goal='Do the thing.')
    trajectory.add_event(
        AgentEvent(
            event_id='plan_1', trace_id='no_action', agent_name='planner',
            event_type='plan', step_index=1, output='Search for the cheapest option.',
        )
    )
    llm = RecordingLLM(
        '{"span_id":"plan_1","step_index":1,"agent_name":"planner","confidence":0.7,'
        '"rationale":"planned the wrong thing","evidence":[],'
        '"corrected_action":{"tool":"search","args":{"q":"refundable"}}}'
    )

    result = AllAtOnceAttributor(llm, propose_corrected_action=True).attribute(trajectory)

    action = result.hypotheses[0].corrected_action
    assert action is not None
    assert action.original is None
    assert action.differs_from_original is None


# --- refusing to guess -------------------------------------------------------------------


def test_unusable_emissions_become_none_rather_than_a_reshaped_guess() -> None:
    """Anything we cannot represent faithfully in the trace's shape is dropped.

    Reshaping a string arg into an invented key, or accepting a nameless tool, produces
    something that looks executable and is not -- the failure mode this field exists to
    prevent. Each of these must come back as "no corrected action".
    """
    unusable = [
        '"just take the other plate"',          # prose instead of an action
        '{"args":{"object":"plate 1"}}',        # no tool named
        '{"tool":"","args":{}}',                # empty tool name
        '{"tool":"take","args":"plate 1"}',     # args not an object
        '{"tool":"take","args":["plate 1"]}',   # args a list
        '[]',                                   # not an object at all
    ]
    for payload in unusable:
        result = AllAtOnceAttributor(
            RecordingLLM(blame_json(payload)), propose_corrected_action=True,
        ).attribute(acting_trajectory())
        assert result.hypotheses[0].corrected_action is None, payload
        assert result.raw['corrected_action']['reason'] == 'declined_by_model', payload


def test_args_alias_is_accepted_from_the_model() -> None:
    """`arguments` is what OpenAI-style tool calls use; normalize rather than lose it."""
    llm = RecordingLLM(blame_json('{"name":"go","arguments":{"to":"drawer"}}'))

    result = AllAtOnceAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    action = result.hypotheses[0].corrected_action
    assert action is not None
    assert action.as_event_input() == {'tool': 'go', 'args': {'to': 'drawer'}}


# --- attributors that cannot produce one -------------------------------------------------


def test_model_free_attributors_never_guess(
    failed_trajectory: AgentTrajectory, failure_mode: FailureMode,
) -> None:
    """Heuristic has no model to ask, so it returns None -- and says why, rather than
    leaving the consumer to read the absence as 'the model declined'."""
    finding = FailureFinding(
        failure_mode=failure_mode, event_id='evt_plan', agent_name='planner',
        step_index=1, confidence=0.6,
    )

    result = HeuristicAttributor().attribute(failed_trajectory, [finding])

    assert result.hypotheses[0].corrected_action is None
    assert result.raw['corrected_action']['reason'] == 'no_llm'
    assert result.raw['corrected_action']['requested'] is False


# --- attributors that need a follow-up call ----------------------------------------------


def test_binary_search_spends_exactly_one_extra_call() -> None:
    """Bisect probes answer upper/lower; there is no slot for an action in that schema."""
    trajectory = acting_trajectory()
    probes = '{"half":"lower","confidence":0.8,"rationale":"the take failed"}'
    llm = RecordingLLM(probes)
    baseline = BinarySearchAttributor(llm).attribute(trajectory)
    calls_without = len(llm.calls)
    assert baseline.hypotheses[0].corrected_action is None
    assert baseline.raw['corrected_action']['reason'] == 'not_requested'

    llm2 = RecordingLLM(
        probes,
        action_response='{"corrected_action":{"tool":"take","args":{"object":"plate 1"}}}',
    )
    result = BinarySearchAttributor(llm2, propose_corrected_action=True).attribute(trajectory)

    assert len(llm2.calls) == calls_without + 1
    assert llm2.action_requests == 1
    action = result.hypotheses[0].corrected_action
    assert action is not None
    assert action.source == 'binary_search'
    assert action.tool == 'take'
    assert result.raw['corrected_action']['reason'] == 'emitted'


def test_binary_search_reports_an_unparseable_proposal_without_inventing_one() -> None:
    probes = '{"half":"lower","confidence":0.8,"rationale":"x"}'
    llm = RecordingLLM(probes, action_response='not json')

    result = BinarySearchAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    assert result.hypotheses[0].corrected_action is None
    assert result.raw['corrected_action']['reason'] in {
        'unparseable_response', 'declined_by_model',
    }


def test_counterfactual_asks_once_for_the_winner_not_once_per_candidate() -> None:
    """K probes rank the candidates; only the top one is worth naming a replacement for."""
    trajectory = acting_trajectory()
    probe = '{"rescue_probability":0.9,"confidence":0.8,"rationale":"wrong object"}'
    llm = RecordingLLM(probe)
    CounterfactualAttributor(llm, max_candidates=3).attribute(trajectory)
    calls_without = len(llm.calls)

    llm2 = RecordingLLM(
        probe,
        action_response='{"corrected_action":{"tool":"take","args":{"object":"plate 1"}}}',
    )
    result = CounterfactualAttributor(
        llm2, max_candidates=3, propose_corrected_action=True,
    ).attribute(trajectory)

    assert len(llm2.calls) == calls_without + 1
    assert llm2.action_requests == 1
    assert result.hypotheses[0].corrected_action is not None
    assert result.hypotheses[0].corrected_action.source == 'counterfactual'


# --- composition -------------------------------------------------------------------------


def test_ensemble_carries_the_action_through_the_merge() -> None:
    """Rationales merge by concatenation; two tool calls have no average, so ONE wins --
    the most confident one -- and its provenance survives in `source`."""

    class Fixed:
        def __init__(self, id_: str, blame: Blame) -> None:
            self.id = id_
            self._blame = blame

        def attribute(self, trajectory: Any, findings: Any = None) -> Any:
            from agentdebug.diagnose.attribute import AttributionResult

            return AttributionResult(method=self.id, hypotheses=[self._blame])

    weak = Blame(
        span_id='call_2', step_index=2, agent_name='solver', confidence=0.3,
        rationale='weak', sources=['weak'],
        corrected_action=CorrectedAction(tool='go', args={'to': 'shelf'}, source='weak'),
    )
    strong = Blame(
        span_id='call_2', step_index=2, agent_name='solver', confidence=0.9,
        rationale='strong', sources=['strong'],
        corrected_action=CorrectedAction(
            tool='take', args={'object': 'plate 1'}, source='strong',
        ),
    )
    silent = Blame(
        span_id='call_2', step_index=2, agent_name='solver', confidence=0.95,
        rationale='no action offered', sources=['silent'],
    )

    for merge in ('borda', 'bayesian'):
        merged = EnsembleAttributor(
            [Fixed('weak', weak), Fixed('strong', strong), Fixed('silent', silent)],
            merge=merge,
        ).attribute(acting_trajectory())
        action = merged.hypotheses[0].corrected_action
        assert action is not None, merge
        # A backend that offered nothing does not erase one that did, even at higher
        # confidence -- absence is not a competing opinion about the action.
        assert action.tool == 'take', merge
        assert action.source == 'strong', merge
        # Same key, same meaning, on every attributor -- and it points at the backend that
        # actually offered the action, not at the ensemble that merely forwarded it.
        assert merged.raw['corrected_action']['reason'] == 'forwarded_from_backend', merge
        assert merged.raw['corrected_action']['source'] == 'strong', merge


def test_ensemble_of_silent_backends_stays_none() -> None:
    class Silent:
        id = 'silent'

        def attribute(self, trajectory: Any, findings: Any = None) -> Any:
            from agentdebug.diagnose.attribute import AttributionResult

            return AttributionResult(
                method=self.id,
                hypotheses=[Blame(
                    span_id='call_2', step_index=2, agent_name='solver',
                    confidence=0.5, rationale='no action', sources=[self.id],
                )],
            )

    merged = EnsembleAttributor([Silent()]).attribute(acting_trajectory())
    assert merged.hypotheses[0].corrected_action is None
    assert merged.raw['corrected_action']['reason'] == 'no_backend_offered_one'


def test_reanchoring_to_a_detector_event_keeps_the_action() -> None:
    """`_prefer_supported_finding` rebuilds the Blame. A rebuild that dropped the field
    would be indistinguishable from an attributor that declined to produce one."""
    trajectory = acting_trajectory()
    finding = FailureFinding(
        failure_mode=FailureMode(
            mode_id='exec.wrong_object', name='Wrong object', family='execution',
            description='Acted on an object that is not there.',
        ),
        event_id='thought_2', agent_name='solver', step_index=2,
        evidence=['object not present'],
    )
    llm = RecordingLLM(blame_json('{"tool":"take","args":{"object":"plate 1"}}'))

    result = AllAtOnceAttributor(
        llm, propose_corrected_action=True,
    ).attribute(trajectory, [finding])

    assert result.hypotheses[0].span_id == 'thought_2'
    assert 'detector_event_anchor' in result.hypotheses[0].sources
    assert result.hypotheses[0].corrected_action is not None
    assert result.hypotheses[0].corrected_action.tool == 'take'


def test_step_by_step_attaches_the_action_to_the_step_it_blamed() -> None:
    llm = RecordingLLM(
        '{"is_failure_step":true,"confidence":0.8,"rationale":"wrong object",'
        '"evidence":["object not present"],'
        '"corrected_action":{"tool":"take","args":{"object":"plate 1"}}}'
    )

    result = StepByStepAttributor(
        llm, propose_corrected_action=True,
    ).attribute(acting_trajectory())

    top = result.hypotheses[0]
    assert top.corrected_action is not None
    assert top.corrected_action.source == 'step_by_step'
    assert result.raw['corrected_action']['reason'] == 'emitted'
