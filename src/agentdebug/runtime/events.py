"""In-process pub/sub event bus for live trace introspection.

The bus is intentionally simple: synchronous fan-out, bounded queue per
subscriber, and never raises out of a publish call. Detectors that need to run
on a streaming trace subscribe here; offline detectors continue to walk the
``AgentTrajectory`` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, DefaultDict, Dict, List, Optional
from uuid import uuid4

from agentdebug.schema.models import AgentEvent, AgentTrajectory, DiagnosticReport

LOG = logging.getLogger('agentdebug.events')

EventHandler = Callable[['BusEvent'], None]


@dataclass(frozen=True)
class BusEvent:
    """Wrapper passed to bus subscribers.

    The ``kind`` field is one of: ``trace.start``, ``trace.event``,
    ``trace.end``, ``analysis.report``.
    """

    kind: str
    trajectory: Optional[AgentTrajectory] = None
    event: Optional[AgentEvent] = None
    report: Optional[DiagnosticReport] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EventSubscription:
    """Returned by :meth:`EventBus.subscribe`; call ``.unsubscribe()`` to detach."""

    id: str
    kind: str
    handler: EventHandler
    _bus: 'EventBus'

    def unsubscribe(self) -> None:
        self._bus._remove(self)


class EventBus:
    """A tiny synchronous pub/sub bus.

    The bus never raises during ``publish`` — handler exceptions are logged and
    a per-handler error counter is incremented. Misbehaving handlers are
    auto-detached after ``error_budget`` consecutive failures so a broken
    detector cannot wedge a live agent.
    """

    KIND_ANY = '*'

    def __init__(self, *, error_budget: int = 5) -> None:
        self._subs: DefaultDict[str, List[EventSubscription]] = defaultdict(list)
        self._error_counts: Dict[str, int] = {}
        self._error_budget = error_budget

    def subscribe(self, kind: str, handler: EventHandler) -> EventSubscription:
        sub = EventSubscription(
            id=f'sub_{uuid4().hex}',
            kind=kind,
            handler=handler,
            _bus=self,
        )
        self._subs[kind].append(sub)
        self._error_counts[sub.id] = 0
        return sub

    def publish(self, evt: BusEvent) -> None:
        # Fan-out to exact-kind subscribers AND wildcard subscribers.
        for sub in list(self._subs.get(evt.kind, ())):
            self._invoke(sub, evt)
        for sub in list(self._subs.get(self.KIND_ANY, ())):
            self._invoke(sub, evt)

    def subscribers(self, kind: str) -> int:
        return len(self._subs.get(kind, ()))

    def _invoke(self, sub: EventSubscription, evt: BusEvent) -> None:
        try:
            sub.handler(evt)
            self._error_counts[sub.id] = 0
        except Exception as exc:  # pragma: no cover - defensive only
            LOG.warning(
                'event handler %s raised on kind=%s: %s', sub.id, evt.kind, exc
            )
            self._error_counts[sub.id] = self._error_counts.get(sub.id, 0) + 1
            if self._error_counts[sub.id] >= self._error_budget:
                LOG.warning(
                    'detaching subscription %s after %d failures',
                    sub.id,
                    self._error_budget,
                )
                self._remove(sub)

    def _remove(self, sub: EventSubscription) -> None:
        bucket = self._subs.get(sub.kind, [])
        self._subs[sub.kind] = [s for s in bucket if s.id != sub.id]
        self._error_counts.pop(sub.id, None)


# A process-global default bus. Tracers that don't take an explicit bus argument
# use this one; tests construct their own.
DEFAULT_BUS = EventBus()
