"""Plugin protocols and metadata for AgentDebugX extension points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Protocol

PluginType = Literal['ingest', 'analysis', 'inspect', 'action']


@dataclass(frozen=True)
class PluginSpec:
    """Lightweight public description of an installed AgentDebugX plugin."""

    plugin_id: str
    plugin_type: PluginType
    display_name: str
    version: str = '0'
    capabilities: List[str] = field(default_factory=list)
    default_enabled: bool = True
    user_visible: bool = True
    metadata: Dict[str, object] = field(default_factory=dict)


class PluginProvider(Protocol):
    """Protocol for objects that can advertise plugin metadata."""

    def plugin_spec(self) -> PluginSpec:
        ...
