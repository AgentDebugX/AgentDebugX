"""LangChain / LangGraph adapter.

Provides ``LangChainCallbackAdapter``, a ``BaseCallbackHandler`` subclass that
records LangChain/LangGraph events into an AgentDebugX trajectory.

We *defer* the import of ``langchain_core`` so importing this module never fails
when LangChain is not installed. Users see a clear error only if they actually
construct the adapter without LangChain installed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.schema import AgentTrajectory, EventType
from agentdebug.ingest.recorder import AgentDebug


def _load_base_callback_handler() -> type:
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except ImportError as exc:  # pragma: no cover - exercised in docs
        raise ImportError(
            'LangChainCallbackAdapter requires `langchain_core`. '
            'Install with `pip install langchain-core`.'
        ) from exc
    return cast(type, BaseCallbackHandler)


class LangChainCallbackAdapter:
    """Bridge LangChain/LangGraph callbacks into an AgentDebugX trajectory.

    Usage:

    >>> from agentdebug import AgentDebug
    >>> from agentdebug.adapters.langgraph import LangChainCallbackAdapter
    >>> debugger = AgentDebug()
    >>> trajectory = debugger.start_trace(goal='...', framework='langgraph')
    >>> handler = LangChainCallbackAdapter(debugger, trajectory)
    >>> graph.invoke(state, config={'callbacks': [handler]})
    >>> debugger.finish_trace(trajectory, success=True)
    """

    framework = 'langgraph'
    
    _debugger: AgentDebug
    _trajectory: AgentTrajectory
    _step_counter: int
    _top_level_runs: set

    def __new__(cls, debugger: AgentDebug, trajectory: AgentTrajectory) -> Any:
        base = _load_base_callback_handler()
        # Dynamically build a subclass of BaseCallbackHandler whose hooks call
        # our recording functions. We keep the implementation here to avoid a
        # module-level dependency on langchain_core.
        impl = type(
            '_LangChainAdapterImpl',
            (base,),
            {
                'on_chain_start': cls._on_chain_start,
                'on_chain_end': cls._on_chain_end,
                'on_chain_error': cls._on_chain_error,
                'on_tool_start': cls._on_tool_start,
                'on_tool_end': cls._on_tool_end,
                'on_tool_error': cls._on_tool_error,
                'on_llm_start': cls._on_llm_start,
                'on_llm_end': cls._on_llm_end,
                'on_llm_error': cls._on_llm_error,
                'on_agent_action': cls._on_agent_action,
                'on_agent_finish': cls._on_agent_finish,
                'on_retriever_start': cls._on_retriever_start,
                'on_retriever_end': cls._on_retriever_end,
                'on_retriever_error': cls._on_retriever_error,
                '_step_counter': 0,
                '_top_level_runs': set(),
            },
        )
        inst = impl()
        inst._debugger = debugger
        inst._trajectory = trajectory
        return inst

    # ------------- hook implementations -------------

    @staticmethod
    def _next_step(handler: Any) -> int:
        handler._step_counter += 1
        return int(handler._step_counter)

    @staticmethod
    def _record(
        handler: Any,
        *,
        event_type: EventType,
        agent_name: str,
        module: Optional[str],
        input: Any = None,
        output: Any = None,
        error: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        run_id: Optional[Any] = None,
        **metadata: Any,
    ) -> None:
        meta: Dict[str, Any] = dict(metadata)
        if run_id is not None:
            meta['run_id'] = str(run_id)
        handler._debugger.record_event(
            handler._trajectory,
            event_type=event_type,
            agent_name=agent_name,
            module=module,
            step_index=LangChainCallbackAdapter._next_step(handler),
            input=input,
            output=output,
            error=error,
            parent_event_id=parent_event_id,
            **meta,
        )

    # The keyword arguments accepted by these methods match the signature of
    # BaseCallbackHandler; we use **kwargs for compatibility across LangChain
    # versions which add or rename fields.

    def _on_chain_start(  # type: ignore[no-untyped-def]
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        if parent_run_id:
            return  # inner node — state already captured by llm/tool events
        self._top_level_runs.add(str(run_id))
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.AGENT_STEP,
            agent_name=str(((serialized or {}).get('name')) or 'chain'),
            module='planning',
            input=inputs,
            run_id=run_id,
            event_class='_on_chain_start'
        )

    def _on_chain_end(self, outputs, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        if str(run_id) not in self._top_level_runs:
            return
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.OBSERVATION,
            agent_name='chain',
            module='planning',
            output=outputs,
            run_id=run_id,
            event_class='_on_chain_end'
        )

    def _on_chain_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        if str(run_id) not in self._top_level_runs:
            return
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.ERROR,
            agent_name='chain',
            module='system',
            error=str(error),
            run_id=run_id,
            event_class='_on_chain_error'
        )

    def _on_tool_start(  # type: ignore[no-untyped-def]
        self, serialized, input_str, *, run_id, **kwargs
    ):
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.TOOL_CALL,
            agent_name=str(((serialized or {}).get('name')) or 'tool'),
            module='action',
            input=input_str,
            run_id=run_id,
            event_class='_on_tool_start'
        )

    def _on_tool_end(self, output, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.TOOL_RESULT,
            agent_name='tool',
            module='action',
            output=output,
            run_id=run_id,
            event_class='_on_tool_end'
        )

    def _on_tool_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.TOOL_RESULT,
            agent_name='tool',
            module='action',
            error=str(error),
            run_id=run_id,
            event_class='_on_tool_error'
        )

    def _on_llm_start(  # type: ignore[no-untyped-def]
        self, serialized, prompts, *, run_id, **kwargs
    ):
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.LLM_CALL,
            agent_name=str(((serialized or {}).get('name')) or 'llm'),
            module='planning',
            input=prompts,
            run_id=run_id,
            event_class='_on_llm_start'
        )

    def _on_llm_end(self, response, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.LLM_RESPONSE,
            agent_name='llm',
            module='planning',
            output=str(response)[:1000],
            run_id=run_id,
            event_class='_on_llm_end'
        )

    def _on_llm_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.ERROR,
            agent_name='llm',
            module='system',
            error=str(error),
            run_id=run_id,
            event_class='_on_llm_error'
        )

    def _on_agent_action(self, action, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.PLAN,
            agent_name='agent',
            module='planning',
            output=str(action)[:1000],
            run_id=run_id,
            event_class='_on_agent_action'
        )

    def _on_agent_finish(self, finish, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.OBSERVATION,
            agent_name='agent',
            module='planning',
            output=str(finish)[:1000],
            run_id=run_id,
            event_class='_on_agent_finish'
        )

    def _on_retriever_start(  # type: ignore[no-untyped-def]
        self, serialized, query, *, run_id, **kwargs
    ):
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.MEMORY_READ,
            agent_name=str(((serialized or {}).get('name')) or 'retriever'),
            module='memory',
            input=query,
            run_id=run_id,
            event_class='_on_retriever_start'
        )

    def _on_retriever_end(self, documents, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.MEMORY_READ,
            agent_name='retriever',
            module='memory',
            output=str(documents)[:1000],
            run_id=run_id,
            event_class='_on_retriever_end'
        )

    def _on_retriever_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        LangChainCallbackAdapter._record(
            self,
            event_type=EventType.ERROR,
            agent_name='retriever',
            module='memory',
            error=str(error),
            run_id=run_id,
            event_class='_on_retriever_error'
        )


class LangGraphAdapter:
    """LangGraph adapter, structural :class:`FrameworkAdapter` (Protocol)."""

    framework = 'langgraph'

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:  # noqa: D401
        try:
            _load_base_callback_handler()
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
                'Create a trajectory, then pass '
                '`LangChainCallbackAdapter(debugger, trajectory)` in '
                "config={'callbacks': [handler]} when invoking your graph."
            ),
        )


_: FrameworkAdapter = LangGraphAdapter()  # static structural check

__all__ = ['LangChainCallbackAdapter', 'LangGraphAdapter']
