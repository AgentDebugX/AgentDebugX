"""Small helpers for instrumenting plain Python functions."""

from __future__ import annotations

import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, cast

from agentdebug.schema import EventType
from agentdebug.ingest.recorder import TraceSession

F = TypeVar('F', bound=Callable[..., Any])


def traced_tool(
    session: TraceSession,
    agent_name: str = 'tool',
    tool_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Record a function invocation as tool.call/tool.result events."""

    def decorator(func: F) -> F:
        name = tool_name or func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            call_event = session.record(
                EventType.TOOL_CALL,
                agent_name=agent_name,
                input={'tool': name, 'args': repr(args), 'kwargs': repr(kwargs)},
            )
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                session.record(
                    EventType.TOOL_RESULT,
                    agent_name=agent_name,
                    parent_event_id=call_event.event_id,
                    error=str(exc),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    tool=name,
                )
                raise
            session.record(
                EventType.TOOL_RESULT,
                agent_name=agent_name,
                parent_event_id=call_event.event_id,
                output=result,
                duration_ms=(time.perf_counter() - started) * 1000,
                tool=name,
            )
            return result

        return cast(F, wrapper)

    return decorator
