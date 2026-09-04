"""``fallback=NO_FALLBACK`` turns the silent heuristic substitute into an exception."""
from __future__ import annotations

import pytest

from agentdebug.diagnose.attribute import (
    NO_FALLBACK,
    AllAtOnceAttributor,
    AttributionUnavailable,
    BinarySearchAttributor,
    CounterfactualAttributor,
    HeuristicAttributor,
    NoFallback,
    StepByStepAttributor,
)
from agentdebug.schema.models import FailureFinding, FailureMode
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = None


class _DeadLLM:
    """Every call fails, the way a gateway outage or a 400 looks to the attributor."""

    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.calls = 0

    def complete(self, **kwargs):  # noqa: ANN003 - mirrors the client protocol
        self.calls += 1
        if self.text is None:
            raise RuntimeError('provider unavailable')
        return _Reply(self.text)


def _trajectory() -> AgentTrajectory:
    events = [
        AgentEvent(trace_id='t', event_type=EventType.AGENT_STEP, step_index=0, output='look'),
        AgentEvent(trace_id='t', event_type=EventType.TOOL_RESULT, step_index=0, output='desk'),
        AgentEvent(trace_id='t', event_type=EventType.AGENT_STEP, step_index=1, output='done'),
    ]
    return AgentTrajectory(trace_id='t', goal='find the cd', events=events)


def _findings(trajectory: AgentTrajectory) -> list[FailureFinding]:
    mode = FailureMode(mode_id='planning_error', name='Planning error', family='planning',
                       description='The first step was the wrong one.')
    return [FailureFinding(failure_mode=mode, event_id=trajectory.events[0].event_id,
                           step_index=0, confidence=0.7, evidence=['wrong first step'])]


@pytest.mark.parametrize('llm', [_DeadLLM(), _DeadLLM('no json here')],
                         ids=['provider_error', 'no_json'])
def test_all_at_once_raises_instead_of_substituting(monkeypatch, llm) -> None:
    monkeypatch.setattr('agentdebug.diagnose.attribute.attribution.time.sleep', lambda *_: None)
    traj = _trajectory()
    default = AllAtOnceAttributor(llm).attribute(traj, _findings(traj))
    assert default is not None, 'the default path still returns a heuristic result'
    with pytest.raises(AttributionUnavailable, match='NO_FALLBACK'):
        AllAtOnceAttributor(llm, fallback=NO_FALLBACK).attribute(traj, _findings(traj))


@pytest.mark.parametrize('cls', [BinarySearchAttributor, CounterfactualAttributor])
def test_search_attributors_raise_instead_of_substituting(monkeypatch, cls) -> None:
    monkeypatch.setattr('agentdebug.diagnose.attribute.attribution.time.sleep', lambda *_: None)
    attributor = cls(_DeadLLM(), fallback=NO_FALLBACK)
    assert attributor.fallback is NO_FALLBACK
    traj = _trajectory()
    with pytest.raises(AttributionUnavailable):
        attributor.attribute(traj, _findings(traj))


def test_step_by_step_accepts_the_sentinel(monkeypatch) -> None:
    """A dead LLM makes step_by_step skip steps rather than fall back, so only the
    sentinel's presence is asserted here; its fallback path is the empty-findings one."""
    monkeypatch.setattr('agentdebug.diagnose.attribute.attribution.time.sleep', lambda *_: None)
    attributor = StepByStepAttributor(_DeadLLM(), fallback=NO_FALLBACK)
    assert attributor.fallback is NO_FALLBACK


def test_sentinel_is_an_attributor_that_only_refuses() -> None:
    assert isinstance(NO_FALLBACK, NoFallback) and NO_FALLBACK.id == 'no_fallback'
    assert not isinstance(NO_FALLBACK, HeuristicAttributor)
    with pytest.raises(AttributionUnavailable):
        NO_FALLBACK.attribute(_trajectory())
