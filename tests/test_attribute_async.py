from __future__ import annotations

import asyncio
import threading

import pytest

from agentdebug.diagnose.attribute import (
    HeuristicAttributor,
    attribute_async,
    attribute_many_async,
    supports_native_async,
)
from agentdebug.diagnose.attribute.attribution import AttributionResult, Blame
from agentdebug.schema import AgentTrajectory, FailureFinding, FailureMode


def _blame(span_id: str, *, step_index: int = 1, confidence: float = 0.5) -> Blame:
    """Blame requires span_id, step_index, agent_name, confidence and rationale."""
    return Blame(
        span_id=span_id,
        step_index=step_index,
        agent_name='a',
        confidence=confidence,
        rationale='test fixture',
    )


def _result(*hypotheses: Blame, method: str = 'test') -> AttributionResult:
    return AttributionResult(method=method, hypotheses=list(hypotheses))


class _SyncOnlyAttributor:
    """Stands in for every attributor shipped today, and every third-party one."""

    id = 'sync_only'
    requires_findings = False

    def __init__(self) -> None:
        self.threads: list[int] = []
        self.calls = 0

    def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        self.calls += 1
        self.threads.append(threading.get_ident())
        return _result(_blame(f'evt_{self.calls}'))


class _NativeAsyncAttributor:
    """Opts into the optional coroutine hook; must never be run in a thread."""

    id = 'native'
    requires_findings = False

    def __init__(self) -> None:
        self.threads: list[int] = []

    def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        raise AssertionError('the sync path must not be used when attribute_async exists')

    async def attribute_async(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        self.threads.append(threading.get_ident())
        return _result(_blame('evt_native', confidence=0.9))


class _FakeAsyncNameAttributor:
    """`attribute_async` exists but is NOT a coroutine function -- awaiting it would fail."""

    id = 'fake_async'
    requires_findings = False

    def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        return _result(_blame('evt_sync'))

    def attribute_async(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        raise AssertionError('a non-coroutine attribute_async must not be awaited')


class _ExplodingAttributor:
    id = 'exploding'
    requires_findings = False

    def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
        raise RuntimeError('provider returned 503')


def test_supports_native_async_requires_an_actual_coroutine() -> None:
    assert supports_native_async(_NativeAsyncAttributor()) is True
    assert supports_native_async(_SyncOnlyAttributor()) is False
    # The trap: present, callable, correctly named, and still not awaitable.
    assert supports_native_async(_FakeAsyncNameAttributor()) is False


@pytest.mark.asyncio
async def test_sync_attributor_runs_off_the_event_loop_thread(
    failed_trajectory: AgentTrajectory,
) -> None:
    attributor = _SyncOnlyAttributor()

    result = await attribute_async(attributor, failed_trajectory)

    assert result.hypotheses[0].span_id == 'evt_1'
    assert attributor.threads == [attributor.threads[0]]
    assert attributor.threads[0] != threading.get_ident(), 'must not block the loop thread'


@pytest.mark.asyncio
async def test_native_async_attributor_is_awaited_directly(
    failed_trajectory: AgentTrajectory,
) -> None:
    attributor = _NativeAsyncAttributor()

    result = await attribute_async(attributor, failed_trajectory)

    assert result.hypotheses[0].span_id == 'evt_native'
    # No thread hop: a natively-async attributor should not pay for one.
    assert attributor.threads == [threading.get_ident()]


@pytest.mark.asyncio
async def test_a_non_coroutine_attribute_async_falls_back_to_the_sync_path(
    failed_trajectory: AgentTrajectory,
) -> None:
    result = await attribute_async(_FakeAsyncNameAttributor(), failed_trajectory)
    assert result.hypotheses[0].span_id == 'evt_sync'


@pytest.mark.asyncio
async def test_findings_still_reach_a_requires_findings_attributor(
    failed_trajectory: AgentTrajectory,
    failure_mode: FailureMode,
) -> None:
    finding = FailureFinding(
        failure_mode=failure_mode,
        event_id='evt_plan',
        agent_name='planner',
        step_index=1,
        confidence=0.4,
    )

    with_findings = await attribute_async(HeuristicAttributor(), failed_trajectory, [finding])
    assert with_findings.hypotheses[0].span_id == 'evt_plan'

    # And the documented trap survives the async wrapper: no findings means empty by
    # construction, not a clean trajectory.
    without = await attribute_async(HeuristicAttributor(), failed_trajectory)
    assert without.hypotheses == []


@pytest.mark.asyncio
async def test_many_preserves_input_order(failed_trajectory: AgentTrajectory) -> None:
    class _OutOfOrderAttributor:
        id = 'out_of_order'
        requires_findings = False

        def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
            position = int(trajectory.trace_id.removeprefix('trace_'))
            threading.Event().wait((5 - position) * 0.01)
            return _result(_blame(trajectory.trace_id))

    trajectories = []
    for index in range(5):
        trajectory = failed_trajectory.prefix(len(failed_trajectory.events))
        trajectory.trace_id = f'trace_{index}'
        trajectories.append(trajectory)

    results = await attribute_many_async(
        _OutOfOrderAttributor(), trajectories, concurrency=5
    )

    assert len(results) == 5
    assert [r.hypotheses[0].span_id for r in results] == [
        trajectory.trace_id for trajectory in trajectories
    ]


@pytest.mark.asyncio
async def test_many_respects_the_concurrency_ceiling(
    failed_trajectory: AgentTrajectory,
) -> None:
    peak = 0
    live = 0
    lock = threading.Lock()

    class _Counting:
        id = 'counting'
        requires_findings = False

        def attribute(self, trajectory, findings=None):  # noqa: ANN001, ANN201
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            try:
                # Long enough that an unbounded gather would overlap all ten.
                threading.Event().wait(0.05)
            finally:
                with lock:
                    live -= 1
            return _result()

    await attribute_many_async(_Counting(), [failed_trajectory] * 10, concurrency=3)

    assert peak <= 3, f'concurrency ceiling of 3 was exceeded: peak {peak}'


@pytest.mark.asyncio
async def test_many_can_return_failures_instead_of_swallowing_them(
    failed_trajectory: AgentTrajectory,
) -> None:
    """The point of return_exceptions: a 503 must not read as "found nothing".

    A downstream harness recorded provider errors as empty attributions, which is
    indistinguishable from a genuine verdict of no-error and silently understated one
    attributor against the others.
    """
    results = await attribute_many_async(
        _ExplodingAttributor(), [failed_trajectory] * 2, return_exceptions=True
    )

    assert len(results) == 2
    assert all(isinstance(r, RuntimeError) for r in results)
    assert 'provider returned 503' in str(results[0])


@pytest.mark.asyncio
async def test_many_propagates_by_default(failed_trajectory: AgentTrajectory) -> None:
    with pytest.raises(RuntimeError, match='provider returned 503'):
        await attribute_many_async(_ExplodingAttributor(), [failed_trajectory])


@pytest.mark.asyncio
async def test_many_rejects_misaligned_findings(failed_trajectory: AgentTrajectory) -> None:
    with pytest.raises(ValueError, match='matched by index'):
        await attribute_many_async(
            _SyncOnlyAttributor(), [failed_trajectory] * 3, findings=[None, None]
        )


@pytest.mark.asyncio
async def test_many_rejects_a_nonsense_concurrency(
    failed_trajectory: AgentTrajectory,
) -> None:
    with pytest.raises(ValueError, match='concurrency must be'):
        await attribute_many_async(
            _SyncOnlyAttributor(), [failed_trajectory], concurrency=0
        )


@pytest.mark.asyncio
async def test_many_on_an_empty_sequence_is_empty_not_an_error() -> None:
    assert await attribute_many_async(_SyncOnlyAttributor(), []) == []


@pytest.mark.asyncio
async def test_usage_survives_both_paths(failed_trajectory: AgentTrajectory) -> None:
    """Cost accounting must not depend on which path ran."""
    sync_result = await attribute_async(_SyncOnlyAttributor(), failed_trajectory)
    native_result = await attribute_async(_NativeAsyncAttributor(), failed_trajectory)

    for result in (sync_result, native_result):
        assert result.usage is not None
        assert result.usage.total_tokens == 0


def test_the_sync_api_is_untouched(failed_trajectory: AgentTrajectory) -> None:
    """No existing caller has to change: attribute() still works with no event loop."""
    assert _SyncOnlyAttributor().attribute(failed_trajectory).hypotheses[0].span_id == 'evt_1'
    assert asyncio.get_event_loop_policy() is not None
