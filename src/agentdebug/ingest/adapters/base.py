"""Adapter interfaces for agent frameworks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdebug.ingest.recorder import AgentDebug


@dataclass(frozen=True)
class AdapterStatus:
    framework: str
    implemented: bool
    notes: str


class FrameworkAdapter(Protocol):
    framework: str

    def instrument(self, debugger: AgentDebug) -> AdapterStatus:
        ...
