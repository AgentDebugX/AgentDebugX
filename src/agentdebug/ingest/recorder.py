"""High-level recording API."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Literal, Optional, Type, Union

from agentdebug.core.models import AgentEvent, AgentTrajectory, DiagnosticReport, EventType, utc_now
from agentdebug.core.storage import JsonlTraceStore, TraceStore
from agentdebug.diagnose.analyzers import HeuristicAnalyzer


class AgentDebug:
    """Main entry point for embedding AgentDebugX in an agent system."""

    def __init__(
        self,
        store: Optional[TraceStore] = None,
        analyzer: Optional[HeuristicAnalyzer] = None,
    ) -> None:
        self.store = store or JsonlTraceStore()
        self.analyzer = analyzer or HeuristicAnalyzer()

    def start_trace(
        self,
        goal: Optional[str] = None,
        task_id: Optional[str] = None,
        framework: Optional[str] = None,
        trace_id: Optional[str] = None,
        **metadata: Any,
    ) -> AgentTrajectory:
        trajectory = AgentTrajectory(
            goal=goal,
            task_id=task_id,
            framework=framework,
            metadata=metadata,
        )
        if trace_id is not None:
            trajectory.trace_id = trace_id
        self.record_event(
            trajectory,
            event_type=EventType.RUN_START,
            agent_name='system',
            output={'goal': goal, 'framework': framework},
        )
        return trajectory

    def record_event(
        self,
        trajectory: AgentTrajectory,
        event_type: Union[EventType, str] = EventType.AGENT_STEP,
        agent_name: str = 'agent',
        module: Optional[str] = None,
        step_index: Optional[int] = None,
        parent_event_id: Optional[str] = None,
        input: Any = None,
        output: Any = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        **metadata: Any,
    ) -> AgentEvent:
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        event = AgentEvent(
            trace_id=trajectory.trace_id,
            parent_event_id=parent_event_id,
            agent_name=agent_name,
            event_type=event_type,
            module=module,
            step_index=step_index,
            input=input,
            output=output,
            error=error,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        return trajectory.add_event(event)

    def finish_trace(
        self,
        trajectory: AgentTrajectory,
        success: bool,
        output: Any = None,
        error: Optional[str] = None,
        **metadata: Any,
    ) -> AgentTrajectory:
        trajectory.ended_at = utc_now()
        self.record_event(
            trajectory,
            event_type=EventType.RUN_END if success else EventType.ERROR,
            agent_name='system',
            output=output,
            error=error,
            success=success,
            **metadata,
        )
        self.store.save_trajectory(trajectory)
        return trajectory

    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        report = self.analyzer.analyze(trajectory)
        save_report = getattr(self.store, 'save_report', None)
        if callable(save_report):
            save_report(report)
        return report

    def analyze_trace(self, trace_id: str) -> DiagnosticReport:
        trajectory = self.store.load_trajectory(trace_id)
        if trajectory is None:
            raise KeyError(f'Unknown trace_id: {trace_id}')
        return self.analyze(trajectory)

    def trace(
        self,
        goal: Optional[str] = None,
        task_id: Optional[str] = None,
        framework: Optional[str] = None,
        **metadata: Any,
    ) -> 'TraceSession':
        trajectory = self.start_trace(
            goal=goal,
            task_id=task_id,
            framework=framework,
            **metadata,
        )
        return TraceSession(debugger=self, trajectory=trajectory)


class TraceSession:
    """Context manager around a trajectory.

    Maintains a monotonic step counter so that callers (and instrumentation
    helpers like :func:`agentdebug.instrumentation.traced_tool`) can omit
    ``step_index`` and still have downstream analyzers see properly ordered
    steps. Explicit ``step_index`` always wins; passing ``None`` triggers
    auto-assignment.
    """

    def __init__(self, debugger: AgentDebug, trajectory: AgentTrajectory) -> None:
        self.debugger = debugger
        self.trajectory = trajectory
        self._step_counter = 0

    def _next_step(self) -> int:
        self._step_counter += 1
        return self._step_counter

    def __enter__(self) -> 'TraceSession':
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Literal[False]:
        if exc_value is None:
            self.debugger.finish_trace(self.trajectory, success=True)
            return False
        self.debugger.finish_trace(
            self.trajectory,
            success=False,
            error=f'{exc_type.__name__ if exc_type else "Error"}: {exc_value}',
        )
        return False

    def record(
        self,
        event_type: Union[EventType, str] = EventType.AGENT_STEP,
        agent_name: str = 'agent',
        module: Optional[str] = None,
        step_index: Optional[int] = None,
        parent_event_id: Optional[str] = None,
        input: Any = None,
        output: Any = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        **metadata: Any,
    ) -> AgentEvent:
        if step_index is None:
            step_index = self._next_step()
        return self.debugger.record_event(
            self.trajectory,
            event_type=event_type,
            agent_name=agent_name,
            module=module,
            step_index=step_index,
            parent_event_id=parent_event_id,
            input=input,
            output=output,
            error=error,
            duration_ms=duration_ms,
            **metadata,
        )

    def analyze(self) -> DiagnosticReport:
        return self.debugger.analyze(self.trajectory)
