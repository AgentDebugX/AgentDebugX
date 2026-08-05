"""Awaitable attribution, without requiring any attributor to become async.

Attribution is the slowest stage in a debugging pipeline and the most obviously
parallel: attributing N trajectories, or comparing M attributors on one trajectory,
are both embarrassingly concurrent. But `Attributor.attribute` is synchronous, so
every async caller ends up writing its own `asyncio.to_thread(...)` hop. Downstream
harnesses have each grown a private copy of that line, and copies drift: one forgets
the concurrency cap and opens as many provider connections as there are traces, another
wraps the batch in a bare `except` and reports a provider error as "no hypotheses".

This module is the shared version. It adds nothing to the `Attributor` protocol that
existing implementations must satisfy:

* Sync-only attributors -- every one shipped today, and every third-party one -- work
  unchanged. They are run off the event loop in a worker thread.
* An attributor that *is* natively async may declare an optional `attribute_async`
  coroutine method. When present it is awaited directly and no thread is used. Nothing
  requires it, and `Attributor` does not gain a mandatory member.

The result is the same `AttributionResult` either way, `usage` included, so cost
accounting is unaffected by which path ran.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, List, Optional, Sequence, Union

from agentdebug.diagnose.attribute.attribution import AttributionResult
from agentdebug.schema import AgentTrajectory, FailureFinding

__all__ = ['attribute_async', 'attribute_many_async', 'supports_native_async']

#: Default ceiling on in-flight attributions. Chosen to be small: each one is usually an
#: LLM call, providers rate-limit per key, and an unbounded `gather` over a few hundred
#: traces reliably turns a slow run into a failed one.
DEFAULT_CONCURRENCY = 4


def supports_native_async(attributor: Any) -> bool:
    """Whether `attributor` implements the optional `attribute_async` coroutine method.

    Exposed so a caller can report which path it took rather than guess. A plain
    `hasattr` is not enough: a sync method named `attribute_async` would satisfy it and
    then fail to await, so the callable is also checked for being a coroutine function.
    """
    candidate = getattr(attributor, 'attribute_async', None)
    if candidate is None:
        return False
    return inspect.iscoroutinefunction(candidate)


async def attribute_async(
    attributor: Any,
    trajectory: AgentTrajectory,
    findings: Optional[List[FailureFinding]] = None,
) -> AttributionResult:
    """Await one attribution, natively if the attributor supports it and off-thread if not.

    Equivalent to `attributor.attribute(trajectory, findings)` in every respect except
    that it does not block the event loop.

    Note on `findings`: attributors that declare `requires_findings = True` still require
    them here. Passing `None` to one of those returns an empty result by construction, not
    because the trajectory is clean -- see `HeuristicAttributor.requires_findings`.
    """
    if supports_native_async(attributor):
        return await attributor.attribute_async(trajectory, findings)
    return await asyncio.to_thread(attributor.attribute, trajectory, findings)


async def attribute_many_async(
    attributor: Any,
    trajectories: Sequence[AgentTrajectory],
    findings: Optional[Sequence[Optional[List[FailureFinding]]]] = None,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    return_exceptions: bool = False,
) -> List[Union[AttributionResult, BaseException]]:
    """Attribute many trajectories concurrently, bounded, and in input order.

    `findings` is aligned to `trajectories` by index when given; a `None` entry means no
    findings for that trajectory. Supplying a sequence of a different length is a
    programming error and raises rather than silently attributing the wrong findings to
    the wrong trace.

    `return_exceptions=False` (the default) propagates the first failure, matching
    `asyncio.gather`. Pass `True` to get a list positionally aligned with the input in
    which failures appear as exception objects.

    That flag exists because of a specific failure mode seen downstream: a caller
    comparing attributors wrapped the whole batch in `try/except` and recorded a provider
    error as "this attributor found nothing", which is indistinguishable from a real
    verdict of no-error and quietly understated one attributor against the others. Keeping
    the exception in the result makes an infrastructure failure impossible to confuse with
    a finding. An outage is not a measurement.
    """
    if concurrency < 1:
        raise ValueError(f'concurrency must be >= 1, got {concurrency}')
    if findings is not None and len(findings) != len(trajectories):
        raise ValueError(
            f'findings has length {len(findings)} but there are {len(trajectories)} '
            'trajectories; they are matched by index'
        )
    if not trajectories:
        return []

    limit = asyncio.Semaphore(concurrency)

    async def _one(index: int) -> AttributionResult:
        async with limit:
            per_trace = findings[index] if findings is not None else None
            return await attribute_async(attributor, trajectories[index], per_trace)

    return await asyncio.gather(
        *(_one(i) for i in range(len(trajectories))),
        return_exceptions=return_exceptions,
    )
