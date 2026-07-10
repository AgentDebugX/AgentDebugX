"""Optional OpenTelemetry GenAI export shim.

Translates an :class:`AgentTrajectory` into OTel spans following the GenAI
semantic conventions (``gen_ai.*`` attributes). Best-effort: the entire module
is a no-op when ``opentelemetry-api`` is not installed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.core.models import AgentEvent, AgentTrajectory, EventType
from agentdebug.ingest.recorder import AgentDebug


def _otel_available() -> bool:
    try:
        import opentelemetry  # noqa: F401
        return True
    except ImportError:
        return False


_EVENT_TO_OP: Dict[EventType, str] = {
    EventType.LLM_CALL: 'chat',
    EventType.LLM_RESPONSE: 'chat',
    EventType.TOOL_CALL: 'execute_tool',
    EventType.TOOL_RESULT: 'execute_tool',
    EventType.AGENT_STEP: 'invoke_agent',
    EventType.PLAN: 'invoke_agent',
    EventType.REFLECTION: 'invoke_agent',
    EventType.HANDOFF: 'handoff',
    EventType.MEMORY_READ: 'retrieve_context',
    EventType.MEMORY_WRITE: 'retrieve_context',
    EventType.RUN_START: 'invoke_workflow',
    EventType.RUN_END: 'invoke_workflow',
    EventType.OBSERVATION: 'invoke_agent',
    EventType.ERROR: 'invoke_agent',
    EventType.GUARDRAIL: 'invoke_agent',
    EventType.HUMAN_FEEDBACK: 'invoke_agent',
}


def export_trajectory(
    trajectory: AgentTrajectory,
    *,
    tracer_name: str = 'agentdebug',
    tracer_provider: Optional[Any] = None,
) -> int:
    """Emit OTel spans for a finished trajectory. Returns the number of spans emitted.

    Returns 0 (and emits nothing) when OpenTelemetry is not installed.
    """
    if not _otel_available():
        return 0
    from opentelemetry import trace as ot_trace

    tracer = (tracer_provider or ot_trace.get_tracer_provider()).get_tracer(tracer_name)
    emitted = 0
    with tracer.start_as_current_span(
        f'agentdebug.run/{trajectory.framework or "unknown"}',
        attributes=_root_attributes(trajectory),
    ) as root:
        _set_attr(root, 'agentdebug.trace_id', trajectory.trace_id)
        for event in trajectory.events:
            _emit_event_span(tracer, event)
            emitted += 1
    return emitted


def _emit_event_span(tracer: Any, event: AgentEvent) -> None:
    op = _EVENT_TO_OP.get(event.event_type, 'invoke_agent')
    with tracer.start_as_current_span(
        f'agentdebug.event/{_event_type_value(event.event_type)}',
        attributes=_event_attributes(event, op),
    ) as span:
        if event.error:
            _set_attr(span, 'error.message', str(event.error))


def _root_attributes(trajectory: AgentTrajectory) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        'gen_ai.operation.name': 'invoke_workflow',
        'agentdebug.trace_id': trajectory.trace_id,
        'agentdebug.framework': trajectory.framework or '',
    }
    if trajectory.goal:
        attrs['agentdebug.goal'] = trajectory.goal
    if trajectory.task_id:
        attrs['agentdebug.task_id'] = trajectory.task_id
    return attrs


def _event_attributes(event: AgentEvent, op: str) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        'gen_ai.operation.name': op,
        'agentdebug.event_id': event.event_id,
        'agentdebug.event_type': _event_type_value(event.event_type),
        'agentdebug.agent.name': event.agent_name,
    }
    if event.module is not None:
        attrs['agentdebug.module'] = event.module
    if event.step_index is not None:
        attrs['agentdebug.step_index'] = event.step_index
    if event.duration_ms is not None:
        attrs['agentdebug.duration_ms'] = event.duration_ms
    if event.error:
        attrs['error.type'] = 'AgentEventError'
    return attrs


def _set_attr(span: Any, key: str, value: Any) -> None:
    setter = getattr(span, 'set_attribute', None)
    if callable(setter):
        setter(key, value)


def _event_type_value(event_type: EventType) -> str:
    value = getattr(event_type, 'value', event_type)
    if isinstance(value, str):
        return value
    return str(value)


class OTelExportAdapter:
    """OTel export adapter, structural :class:`FrameworkAdapter` (not subclassed)."""

    framework = 'otel'

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:  # noqa: D401
        if _otel_available():
            return AdapterStatus(
                framework=self.framework,
                implemented=True,
                notes=(
                    'OpenTelemetry detected. Call '
                    'agentdebug.adapters.otel.export_trajectory(trajectory) '
                    'to emit gen_ai.* spans.'
                ),
            )
        return AdapterStatus(
            framework=self.framework,
            implemented=False,
            notes='Install `opentelemetry-api` and `opentelemetry-sdk` to enable.',
        )


_: FrameworkAdapter = OTelExportAdapter()  # static structural check

__all__ = ['OTelExportAdapter', 'export_trajectory']
