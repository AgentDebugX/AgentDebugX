"""CrewAI adapter — subscribe to ``crewai_event_bus`` and record into AgentDebug.

CrewAI's runtime emits typed events through a process-global event bus
(``crewai.events.crewai_event_bus``). The bus has ~50 event types covering
crew lifecycle, task lifecycle, agent execution, LLM calls, tool usage,
flows, memory operations, A2A messages, MCP calls, knowledge ops, and
guardrails. This adapter subscribes to the subset that matters for
debugging and translates each into an :class:`agentdebug.models.AgentEvent`.

Why a class (not a free-function listener)? CrewAI's documented gotcha is
that listeners must be instantiated at module level OR explicitly kept
alive; otherwise the GC reclaims them before the bus fires. We hide that
by exposing a context-manager friendly :class:`CrewAIBridge` that holds
the listener instance for its lifetime.

Usage::

    from agentdebug import AgentDebug, SQLiteTraceStore
    from agentdebug.adapters.crewai import CrewAIBridge

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/errors.sqlite'))
    trajectory = debugger.start_trace(goal='build a marketing plan', framework='crewai')

    with CrewAIBridge(debugger, trajectory):
        crew.kickoff(inputs={...})   # normal CrewAI call; events flow into AgentDebug

    debugger.finish_trace(trajectory, success=True)

The bridge defers the ``crewai`` import, so this module is safe to import
even when CrewAI is not installed.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, List, Literal, NamedTuple, Optional, Tuple, Type

from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.core.models import AgentTrajectory, EventType
from agentdebug.ingest.recorder import AgentDebug

LOG = logging.getLogger('agentdebug.adapters.crewai')


def _import_crewai_events() -> Any:
    """Import crewai.events lazily; raise a clear ImportError if missing."""
    try:
        import crewai.events as crewai_events
    except ImportError as exc:
        raise ImportError(
            'CrewAIBridge requires the `crewai` package. '
            "Install with `pip install 'agentdebugx[crewai]'` or `pip install crewai`."
        ) from exc
    return crewai_events


# Event-type name → (EventType, module) mapping. Class objects are resolved at
# attach-time so this module stays import-free when CrewAI is absent.
_EVENT_MAP: List[Tuple[str, EventType, Optional[str]]] = [
    # (CrewAI event class name, agentdebug EventType, module)
    ('CrewKickoffStartedEvent',     EventType.AGENT_STEP,   'planning'),
    ('CrewKickoffCompletedEvent',   EventType.OBSERVATION,  'planning'),
    ('TaskStartedEvent',            EventType.PLAN,         'planning'),
    ('TaskCompletedEvent',          EventType.OBSERVATION,  'planning'),
    ('AgentExecutionStartedEvent',  EventType.AGENT_STEP,   'planning'),
    ('AgentExecutionCompletedEvent',EventType.OBSERVATION,  'reflection'),
    ('LLMCallStartedEvent',         EventType.LLM_CALL,     'planning'),
    ('LLMCallCompletedEvent',       EventType.LLM_RESPONSE, 'planning'),
    ('ToolUsageStartedEvent',           EventType.TOOL_CALL,    'action'),
    ('ToolUsageFinishedEvent',          EventType.TOOL_RESULT,  'action'),
    ('ToolUsageErrorEvent',             EventType.TOOL_RESULT,  'action'),
    ('MemoryRetrievalCompletedEvent',   EventType.MEMORY_READ,  'memory'),
    ('MemoryRetrievalFailedEvent',      EventType.MEMORY_READ,  'memory'),
    ('MemoryQueryFailedEvent',          EventType.MEMORY_READ,  'memory'),
    ('MemorySaveFailedEvent',           EventType.MEMORY_WRITE, 'memory'),
]

# Per-event field specs: (agent_spec, input_spec, output_spec, error_spec).
#
# Each spec is either None, a tuple of attribute names tried in order (first
# non-None wins), or a callable for cases that require object traversal.
# Tuples carry aliases so a field rename in a future CrewAI version degrades
# gracefully rather than silently returning None.
_Spec = Any  # None | str | Callable[[Any], Optional[str]]


def _role(obj: Any) -> Optional[str]:
    """Extract .role from an Agent/Task object without stringifying the whole object."""
    v = getattr(obj, 'role', None)
    return str(v) if v is not None else None


def _get(event: Any, spec: _Spec) -> Optional[str]:
    if spec is None:
        return None
    if callable(spec):
        try:
            v = spec(event)
            return str(v) if v is not None else None
        except Exception:
            return None
    v = getattr(event, spec, None)
    return str(v) if v is not None else None


class _Fields(NamedTuple):
    agent:  _Spec = None
    input:  _Spec = None
    output: _Spec = None
    error:  _Spec = None


def _agent_via_task(e: Any) -> Optional[str]:
    return _role(getattr(e.task, 'agent', None))


def _agent_via_field(e: Any) -> Optional[str]:
    return _role(e.agent)


_FIELD_MAP: dict[str, _Fields] = {
    'CrewKickoffStartedEvent':      _Fields(agent='crew_name',       input='inputs'),
    'CrewKickoffCompletedEvent':    _Fields(agent='crew_name',       output='output'),
    'TaskStartedEvent':             _Fields(agent=_agent_via_task,   input='context'),
    'TaskCompletedEvent':           _Fields(agent=_agent_via_task,   output='output'),
    'AgentExecutionStartedEvent':   _Fields(agent=_agent_via_field,  input='task_prompt'),
    'AgentExecutionCompletedEvent': _Fields(agent=_agent_via_field,  output='output'),
    'LLMCallStartedEvent':          _Fields(agent='agent_role',      input='messages'),
    'LLMCallCompletedEvent':        _Fields(agent='agent_role',      input='messages',   output='response'),
    'ToolUsageStartedEvent':          _Fields(agent='agent_role',  input='tool_args'),
    'ToolUsageFinishedEvent':         _Fields(agent='agent_role',  input='tool_args',  output='output'),
    'ToolUsageErrorEvent':            _Fields(agent='agent_role',  input='tool_args',  error='error'),
    'MemoryRetrievalCompletedEvent':  _Fields(agent='agent_role',  output='memory_content'),
    'MemoryRetrievalFailedEvent':     _Fields(agent='agent_role',  error='error'),
    'MemoryQueryFailedEvent':         _Fields(agent='agent_role',  input='query',      error='error'),
    'MemorySaveFailedEvent':          _Fields(agent='agent_role',  input='value',      error='error'),
}


class CrewAIBridge:
    """Subscribe to crewai_event_bus and record events into an AgentDebug trajectory.

    Behaves as both a context manager (``with CrewAIBridge(...): ...``) and a
    plain object with ``attach()`` / ``detach()``.
    """

    framework = 'crewai'

    def __init__(
        self,
        debugger: AgentDebug,
        trajectory: AgentTrajectory,
        *,
        bus: Any = None,
    ) -> None:
        self.debugger = debugger
        self.trajectory = trajectory
        self._bus = bus
        self._attached = False
        self._step_counter = 0
        # Keep refs to closures so they don't get GC'd while bus holds them.
        self._handlers: List[Any] = []

    def attach(self) -> 'CrewAIBridge':
        if self._attached:
            return self
        ce = _import_crewai_events()
        bus = self._bus or getattr(ce, 'crewai_event_bus', None)
        if bus is None:
            raise RuntimeError('crewai.events.crewai_event_bus is not available')
        for class_name, et, module in _EVENT_MAP:
            evt_cls = getattr(ce, class_name, None)
            if evt_cls is None:
                LOG.debug('CrewAI event class %s not found; skipping', class_name)
                continue
            handler = self._make_handler(et, module, class_name)
            self._handlers.append(handler)
            bus.on(evt_cls)(handler)
        self._attached = True
        return self

    def detach(self) -> None:
        # CrewAI's event bus does not expose a public unsubscribe surface as
        # of the versions we target. Drop our handler references so they
        # become eligible for GC; the bus will skip dead refs.
        self._handlers.clear()
        self._attached = False

    # Context-manager sugar — equivalent to attach/detach.

    def __enter__(self) -> 'CrewAIBridge':
        return self.attach()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> Literal[False]:
        self.detach()
        return False

    # ---- internals ----

    def _make_handler(
        self,
        event_type: EventType,
        module: Optional[str],
        class_name: str,
    ) -> Any:
        debugger = self.debugger
        trajectory = self.trajectory
        bridge = self

        def _on_event(source: Any, event: Any) -> None:
            try:
                bridge._step_counter += 1
                f = _FIELD_MAP.get(class_name, _Fields())
                # NB: record_event takes metadata via **kwargs, so we pass
                # the per-event tags as flat keyword args. Passing them as
                # `metadata={...}` would wrap them under `metadata['metadata']`.
                debugger.record_event(
                    trajectory,
                    event_type=event_type,
                    agent_name=_get(event, f.agent) or class_name,
                    module=module,
                    step_index=bridge._step_counter,
                    input=_get(event, f.input),
                    output=_get(event, f.output),
                    error=_get(event, f.error),
                    crewai_event_class=class_name,
                    crewai_source_type=type(source).__name__,
                )
            except Exception as exc:  # pragma: no cover - defensive
                LOG.warning(
                    'CrewAIBridge handler for %s raised: %s', class_name, exc
                )

        return _on_event


class CrewAIAdapter:
    """Structural :class:`FrameworkAdapter` — used by `agentdebug doctor`."""

    framework = 'crewai'

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:  # noqa: D401
        try:
            _import_crewai_events()
        except ImportError as exc:
            return AdapterStatus(
                framework=self.framework,
                implemented=False,
                notes=str(exc),
            )
        return AdapterStatus(
            framework=self.framework,
            implemented=True,
            notes=(
                'Create a trajectory, then use '
                '`with CrewAIBridge(debugger, trajectory): crew.kickoff(...)` '
                'to record every Crew/Task/Agent/LLM/Tool event into AgentDebug.'
            ),
        )


_: FrameworkAdapter = CrewAIAdapter()  # static structural check


__all__ = ['CrewAIAdapter', 'CrewAIBridge']
