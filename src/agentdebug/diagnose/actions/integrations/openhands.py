"""OpenHands integration shim.

OpenHands (formerly OpenDevin) is an event-stream-based agent runtime. This
module provides two complementary entry points for embedding AgentDebugX:

1. :class:`OpenHandsBridge` — subscribes to a live ``EventStream`` and records
   every typed Action / Observation into an AgentDebugX trajectory. Use this
   if you want passive observability of an in-flight OpenHands session.
2. :class:`OpenHandsMicroagentContract` — emits the metadata file required
   for OpenHands to load AgentDebugX as a *microagent* the host agent can
   invoke as a tool ("debug my last run"). The generated file is just
   YAML + markdown — the OpenHands runtime does the wiring.

Both paths defer the ``openhands`` import so this module is safe to import
without OpenHands installed.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agentdebug.core.models import AgentTrajectory, EventType
from agentdebug.diagnose.actions.integrations.debug_skill import SHARED_CONTRACT
from agentdebug.ingest.recorder import AgentDebug


@dataclass
class OpenHandsBridge:
    """Bridge an OpenHands EventStream into an AgentDebugX trajectory.

    Construct with a :class:`AgentDebug` and an in-flight
    :class:`AgentTrajectory`; call :meth:`attach` with a
    ``conversation.event_stream`` to subscribe. The subscription stays
    active until you call :meth:`detach`.
    """

    debugger: AgentDebug
    trajectory: AgentTrajectory
    subscriber_id: str = 'agentdebug'
    _unsub: Optional[Any] = None

    def attach(self, event_stream: Any) -> 'OpenHandsBridge':
        try:
            from openhands.events import EventStreamSubscriber
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                'OpenHandsBridge requires `openhands`. Install with '
                "`pip install openhands-ai`."
            ) from exc

        def _on_event(event: Any) -> None:
            self._record_openhands_event(event)

        event_stream.subscribe(
            EventStreamSubscriber.MAIN, _on_event, subscriber_id=self.subscriber_id
        )
        self._unsub = (event_stream, self.subscriber_id)
        return self

    def detach(self) -> None:
        if self._unsub is None:
            return
        event_stream, sid = self._unsub
        unsub = getattr(event_stream, 'unsubscribe', None)
        if callable(unsub):
            unsub(sid)
        self._unsub = None

    def _record_openhands_event(self, event: Any) -> None:
        """Translate an OpenHands event into an AgentDebugX event."""
        cls_name = type(event).__name__
        # Heuristic mapping — OpenHands has many Action/Observation subclasses,
        # but the names are consistent ("CmdRunAction", "MessageAction", etc).
        if cls_name.endswith('Action'):
            event_type = EventType.TOOL_CALL
        elif cls_name.endswith('Observation'):
            event_type = EventType.TOOL_RESULT
        else:
            event_type = EventType.OBSERVATION
        payload: Dict[str, Any] = {
            'openhands_event': cls_name,
        }
        for attr in ('command', 'thought', 'content', 'agent', 'message',
                     'exit_code', 'cause'):
            val = getattr(event, attr, None)
            if val is not None and not callable(val):
                payload[attr] = str(val)[:1000]
        self.debugger.record_event(
            self.trajectory,
            event_type=event_type,
            agent_name=payload.get('agent') or 'openhands',
            module='openhands',
            output=payload,
        )


MICROAGENT_TEMPLATE = textwrap.dedent("""\
    ---
    name: {name}
    type: knowledge
    triggers:
      - debug agent failure
      - root cause analysis
      - which agent caused failure
      - analyze trajectory
      - diagnose trace
      - debug OpenHands run
      - AgentDebugX
    agents:
      - CodeActAgent
      - DelegatorAgent
    ---

    # AgentDebugX microagent

    When the user asks "why did the last agent run fail?" or hands over a
    trajectory file, defer to AgentDebugX's CLI to produce a structured
    diagnostic report. Do not manually inspect raw trace JSON when an
    AgentDebugX importer can parse it.

    AgentDebugX is suggest-only by default. Do NOT auto-apply a recovery
    proposal against a write-class tool unless the user explicitly approves
    and a compensation has been registered (see
    https://github.com/ulab-uiuc/AgentDebugX/blob/main/docs/08_recovery.md).

    __AGENTDEBUG_SHARED_CONTRACT__
""").replace('__AGENTDEBUG_SHARED_CONTRACT__', textwrap.indent(SHARED_CONTRACT, '    '))


@dataclass
class OpenHandsMicroagentContract:
    """Filesystem contract for the OpenHands microagent loader.

    Materialize with :meth:`write` into a microagent directory the
    OpenHands runtime knows about (default: ``.openhands/microagents/``).
    """

    name: str = 'agentdebug'

    def render(self) -> str:
        return MICROAGENT_TEMPLATE.format(name=self.name)

    def write(self, target_dir: Path) -> Path:
        out = target_dir.expanduser() / f'{self.name}.md'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render(), encoding='utf-8')
        return out


__all__ = ['OpenHandsBridge', 'OpenHandsMicroagentContract']
