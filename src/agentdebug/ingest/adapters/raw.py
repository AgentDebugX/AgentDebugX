"""Adapter for raw / vanilla ReAct loops.

For users who write a ``while not done: ...`` loop against the OpenAI or
Anthropic SDK directly (no framework), this adapter exposes:

* :func:`trace_loop` — decorator that wraps a function in a :class:`TraceSession`
  and exposes the active session via :func:`current_session`.
* :func:`mark_step` — record a step boundary inside the wrapped function.

This is the lowest-overhead integration path: no framework dependency, no
monkey-patching.
"""

from __future__ import annotations

import contextvars
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, cast

from agentdebug.schema import EventType
from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.ingest.recorder import AgentDebug, TraceSession

_current_session: contextvars.ContextVar[Optional[TraceSession]] = contextvars.ContextVar(
    'agentdebug_current_session', default=None
)

F = TypeVar('F', bound=Callable[..., Any])


def current_session() -> Optional[TraceSession]:
    """Return the active TraceSession in this context, if any."""
    return _current_session.get()


def trace_loop(
    debugger: AgentDebug,
    *,
    goal: Optional[str] = None,
    framework: str = 'raw',
    task_id: Optional[str] = None,
    success_on_return: bool = True,
) -> Callable[[F], F]:
    """Wrap a function so its execution becomes one trajectory.

    Inside the wrapped function, use :func:`current_session` to grab the active
    :class:`TraceSession` for direct ``trace.record(...)`` calls, or use
    :func:`mark_step` for the common case.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            run_goal = goal or func.__name__
            session = debugger.trace(goal=run_goal, framework=framework, task_id=task_id)
            token = _current_session.set(session)
            with session:
                try:
                    result = func(*args, **kwargs)
                except Exception:
                    raise
                finally:
                    _current_session.reset(token)
            # If the function returned normally, session.__exit__ already marked success.
            return result

        return cast(F, wrapper)

    return decorator


def mark_step(
    *,
    event_type: EventType = EventType.AGENT_STEP,
    agent_name: str = 'agent',
    step_index: Optional[int] = None,
    module: Optional[str] = None,
    input: Any = None,
    output: Any = None,
    error: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **metadata: Any,
) -> None:
    """Record a step boundary on the current session (no-op if none active)."""
    session = current_session()
    if session is None:
        return
    session.record(
        event_type=event_type,
        agent_name=agent_name,
        step_index=step_index,
        module=module,
        input=input,
        output=output,
        error=error,
        duration_ms=duration_ms,
        **metadata,
    )


class RawLoopAdapter:
    """Implements :class:`FrameworkAdapter` via duck-typing (not subclassed because
    ``FrameworkAdapter`` is a ``Protocol``)."""

    framework = 'raw'

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:  # noqa: D401
        # Nothing to attach globally — users opt in by decorating their loop.
        return AdapterStatus(
            framework=self.framework,
            implemented=True,
            notes=(
                'Apply @agentdebug.adapters.raw.trace_loop(debugger) to your '
                'agent loop and call mark_step(...) inside it.'
            ),
        )


_: FrameworkAdapter = RawLoopAdapter()  # static structural check


def timed_block_ms(start: float) -> float:
    """Helper for adapters: ms since a perf_counter start."""
    return (time.perf_counter() - start) * 1000.0


__all__ = [
    'RawLoopAdapter',
    'current_session',
    'mark_step',
    'timed_block_ms',
    'trace_loop',
]
