"""Central plugin registry for AgentDebugX."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Dict, List, Optional

from agentdebug.runtime.plugins.types import PluginSpec, PluginType

_PLUGIN_REGISTRY: Dict[str, PluginSpec] = {}


def register_plugin(spec: PluginSpec) -> None:
    _PLUGIN_REGISTRY[spec.plugin_id] = spec


def register_ingest_plugin(
    plugin_id: str,
    display_name: str,
    *,
    version: str = '0',
    capabilities: Optional[Iterable[str]] = None,
    default_enabled: bool = True,
    user_visible: bool = True,
    **metadata: Any,
) -> PluginSpec:
    return _register(
        plugin_id=plugin_id,
        plugin_type='ingest',
        display_name=display_name,
        version=version,
        capabilities=capabilities,
        default_enabled=default_enabled,
        user_visible=user_visible,
        metadata=metadata,
    )


def register_analysis_plugin(
    plugin_id: str,
    display_name: str,
    *,
    version: str = '0',
    capabilities: Optional[Iterable[str]] = None,
    default_enabled: bool = True,
    user_visible: bool = True,
    **metadata: Any,
) -> PluginSpec:
    return _register(
        plugin_id=plugin_id,
        plugin_type='analysis',
        display_name=display_name,
        version=version,
        capabilities=capabilities,
        default_enabled=default_enabled,
        user_visible=user_visible,
        metadata=metadata,
    )


def register_inspect_plugin(
    plugin_id: str,
    display_name: str,
    *,
    version: str = '0',
    capabilities: Optional[Iterable[str]] = None,
    default_enabled: bool = True,
    user_visible: bool = True,
    **metadata: Any,
) -> PluginSpec:
    return _register(
        plugin_id=plugin_id,
        plugin_type='inspect',
        display_name=display_name,
        version=version,
        capabilities=capabilities,
        default_enabled=default_enabled,
        user_visible=user_visible,
        metadata=metadata,
    )


def register_action_plugin(
    plugin_id: str,
    display_name: str,
    *,
    version: str = '0',
    capabilities: Optional[Iterable[str]] = None,
    default_enabled: bool = True,
    user_visible: bool = True,
    **metadata: Any,
) -> PluginSpec:
    return _register(
        plugin_id=plugin_id,
        plugin_type='action',
        display_name=display_name,
        version=version,
        capabilities=capabilities,
        default_enabled=default_enabled,
        user_visible=user_visible,
        metadata=metadata,
    )


def list_plugins(plugin_type: Optional[PluginType] = None) -> List[PluginSpec]:
    specs = list(_PLUGIN_REGISTRY.values())
    if plugin_type is not None:
        specs = [spec for spec in specs if spec.plugin_type == plugin_type]
    return sorted(specs, key=lambda spec: (spec.plugin_type, spec.display_name, spec.plugin_id))


def _register(
    *,
    plugin_id: str,
    plugin_type: PluginType,
    display_name: str,
    version: str,
    capabilities: Optional[Iterable[str]],
    default_enabled: bool,
    user_visible: bool,
    metadata: Dict[str, Any],
) -> PluginSpec:
    spec = PluginSpec(
        plugin_id=plugin_id,
        plugin_type=plugin_type,
        display_name=display_name,
        version=version,
        capabilities=list(capabilities or []),
        default_enabled=default_enabled,
        user_visible=user_visible,
        metadata=metadata,
    )
    register_plugin(spec)
    return spec
