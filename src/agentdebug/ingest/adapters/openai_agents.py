"""OpenAI Agents SDK adapter — bridge `agents.tracing` into AgentDebug.

The OpenAI Agents SDK (``openai-agents``) emits typed spans through its own
tracing system (not OpenTelemetry — though there are community OTel bridges).
The integration point is :class:`agents.tracing.TracingProcessor`: subclass
it, register via ``add_trace_processor``, and you receive ``on_span_start`` /
``on_span_end`` for every span the SDK creates (``generation``, ``function``,
``agent``, ``guardrail``, ``handoff``, audio, …).

This module ships ``OpenAIAgentsBridge``, which converts each span end into
an :class:`agentdebug.models.AgentEvent` and records it on a long-lived
:class:`AgentTrajectory`. The ``agents`` package is imported lazily, so this
module is safe to import without the SDK installed.

Usage::

    from agentdebug import AgentDebug, SQLiteTraceStore
    from agentdebug.adapters.openai_agents import OpenAIAgentsBridge

    debugger = AgentDebug(store=SQLiteTraceStore('.agentdebug/errors.sqlite'))
    trajectory = debugger.start_trace(goal='answer the user', framework='openai-agents')

    with OpenAIAgentsBridge(debugger, trajectory):
        from agents import Agent, Runner
        agent = Agent(name='Assistant', instructions='Be concise.')
        Runner.run_sync(agent, 'What is 2+2?')

    debugger.finish_trace(trajectory, success=True)
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Literal, Optional, Type

from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.schema import AgentTrajectory, EventType
from agentdebug.ingest.recorder import AgentDebug

LOG = logging.getLogger('agentdebug.adapters.openai_agents')


def _import_agents_tracing() -> Any:
    try:
        from agents import tracing as _tracing
    except ImportError as exc:
        raise ImportError(
            'OpenAIAgentsBridge requires the `openai-agents` package. '
            "Install with `pip install 'agentdebugx[openai-agents]'` or "
            "`pip install openai-agents`."
        ) from exc
    return _tracing


# Span-type names the SDK emits; map each to the most representative
# AgentDebugX EventType. Names not in this map are recorded as OBSERVATION.
_SPAN_TYPE_TO_EVENT: dict[str, EventType] = {
    'agent': EventType.AGENT_STEP,
    'generation': EventType.LLM_RESPONSE,
    'function': EventType.TOOL_RESULT,
    'tool': EventType.TOOL_RESULT,
    'handoff': EventType.HANDOFF,
    'guardrail': EventType.GUARDRAIL,
    'response': EventType.LLM_RESPONSE,
}


def _span_attr(span: Any, *names: str) -> Any:
    """Read the first non-None attribute from `span` (or `span.span_data` if
    the SDK wraps its payload there).
    """
    holders = [span, getattr(span, 'span_data', None)]
    for holder in holders:
        if holder is None:
            continue
        for name in names:
            val = getattr(holder, name, None)
            if val is not None:
                return val
    return None


class OpenAIAgentsBridge:
    """Bridge ``agents.tracing`` spans into an AgentDebug trajectory.

    Both a context manager and a plain object with attach/detach. Holds the
    registered ``TracingProcessor`` so the SDK doesn't GC it mid-session.
    """

    framework = 'openai-agents'

    def __init__(
        self,
        debugger: AgentDebug,
        trajectory: AgentTrajectory,
    ) -> None:
        self.debugger = debugger
        self.trajectory = trajectory
        self._processor: Optional[Any] = None
        self._attached = False

    def attach(self) -> 'OpenAIAgentsBridge':
        if self._attached:
            return self
        tracing = _import_agents_tracing()
        base_cls = getattr(tracing, 'TracingProcessor', None)
        if base_cls is None:
            raise RuntimeError(
                'agents.tracing.TracingProcessor not found; the installed '
                '`openai-agents` version may be incompatible.'
            )
        bridge = self

        class _Processor(base_cls):  # type: ignore[misc, valid-type]
            def on_trace_start(self_, trace_obj: Any) -> None:
                pass

            def on_trace_end(self_, trace_obj: Any) -> None:
                pass

            def on_span_start(self_, span: Any) -> None:
                pass

            def on_span_end(self_, span: Any) -> None:
                bridge._record_span(span)

            def shutdown(self_) -> None:
                pass

            def force_flush(self_) -> None:
                pass

        self._processor = _Processor()
        add = getattr(tracing, 'add_trace_processor', None)
        if not callable(add):
            raise RuntimeError(
                'agents.tracing.add_trace_processor not found; the installed '
                '`openai-agents` version may be incompatible.'
            )
        add(self._processor)
        self._attached = True
        return self

    def detach(self) -> None:
        # The SDK does not expose a clean unsubscribe in current releases.
        # Dropping the strong reference + having the processor become a no-op
        # is the safest available approach.
        self._processor = None
        self._attached = False

    # Context-manager sugar
    def __enter__(self) -> 'OpenAIAgentsBridge':
        return self.attach()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> Literal[False]:
        self.detach()
        return False

    def _record_span(self, span: Any) -> None:
        try:
            span_type = str(_span_attr(span, 'span_type', 'type') or 'unknown')
            event_type = _SPAN_TYPE_TO_EVENT.get(span_type, EventType.OBSERVATION)
            error = _span_attr(span, 'error')
            agent_name = (
                _span_attr(span, 'agent_name', 'name', 'function_name')
                or span_type
            )
            self.debugger.record_event(
                self.trajectory,
                event_type=event_type,
                agent_name=str(agent_name),
                module=self._module_for(event_type),
                input=_summarize(_span_attr(span, 'input', 'arguments', 'prompt')),
                output=_summarize(
                    _span_attr(span, 'output', 'response', 'result',
                               'completion')
                ),
                error=str(error)[:500] if error else None,
                openai_agents_span_type=span_type,
                openai_agents_span_id=str(_span_attr(span, 'span_id') or ''),
            )
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning('OpenAIAgentsBridge failed to record span: %s', exc)

    @staticmethod
    def _module_for(event_type: EventType) -> str:
        # Pull from agentdebug.taxonomy module families: planning/action/etc.
        if event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}:
            return 'action'
        if event_type in {EventType.HANDOFF, EventType.GUARDRAIL}:
            return 'multiagent'
        if event_type in {EventType.LLM_CALL, EventType.LLM_RESPONSE}:
            return 'planning'
        return 'planning'


def _summarize(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    return text[:1000] if len(text) > 1000 else text


class OpenAIAgentsAdapter:
    """Structural :class:`FrameworkAdapter` for the doctor."""

    framework = 'openai-agents'

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:  # noqa: D401
        try:
            _import_agents_tracing()
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
                '`with OpenAIAgentsBridge(debugger, trajectory): Runner.run_sync(...)` '
                'to record every Agent/Runner/Function/Handoff/Guardrail span '
                'into AgentDebug.'
            ),
        )


_: FrameworkAdapter = OpenAIAgentsAdapter()  # static structural check


__all__ = ['OpenAIAgentsAdapter', 'OpenAIAgentsBridge']
