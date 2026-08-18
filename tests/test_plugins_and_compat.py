from __future__ import annotations

import importlib
import sys

import pytest

from agentdebug.diagnose.registry import _metadata_from_mapping, list_components, load_component
from agentdebug.runtime.plugins import registry
from agentdebug.runtime.plugins.types import PluginSpec


def test_plugin_registry_filters_and_sorts(monkeypatch) -> None:
    monkeypatch.setattr(registry, '_PLUGIN_REGISTRY', {})
    registry.register_plugin(
        PluginSpec('z', 'analysis', 'Zulu', capabilities=['diagnose'])
    )
    registry.register_plugin(PluginSpec('a', 'ingest', 'Alpha'))

    assert [spec.plugin_id for spec in registry.list_plugins()] == ['z', 'a']
    assert [spec.plugin_id for spec in registry.list_plugins('ingest')] == ['a']


def test_duplicate_plugin_registration_replaces_metadata(monkeypatch) -> None:
    monkeypatch.setattr(registry, '_PLUGIN_REGISTRY', {})
    registry.register_plugin(PluginSpec('same', 'ingest', 'Old'))
    registry.register_plugin(PluginSpec('same', 'ingest', 'New'))

    assert registry.list_plugins()[0].display_name == 'New'


@pytest.mark.parametrize(
    'payload',
    [
        [],
        {'id': 'x', 'stage': 'unknown', 'name': 'X', 'description': '', 'entrypoint': 'x'},
        {'stage': 'detect'},
    ],
)
def test_invalid_component_manifests_are_rejected(payload) -> None:
    with pytest.raises((ValueError, KeyError)):
        _metadata_from_mapping(payload)


def test_all_builtin_component_entrypoints_load() -> None:
    for component in list_components():
        assert load_component(component.id) is not None


def test_gui_rule_pack_does_not_eagerly_import_gui_taxonomy() -> None:
    module_names = ('agentdebug.runtime.gui_taxonomy', 'agentdebug.gui.taxonomy')
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    module = importlib.reload(
        importlib.import_module('agentdebug.diagnose.detect.rules.packs.gui.rules')
    )

    assert module.build_event_rules() == []
    assert module.build_trajectory_rules() == []
    for module_name in module_names:
        assert module_name not in sys.modules


@pytest.mark.parametrize(
    ('legacy_path', 'canonical_path', 'symbol'),
    [
        ('agentdebug.models', 'agentdebug.schema', 'AgentTrajectory'),
        ('agentdebug.storage', 'agentdebug.runtime', 'SQLiteTraceStore'),
        (
            'agentdebug.deep',
            'agentdebug.diagnose.profiles.deepdebug',
            'DeepDebugAnalyzer',
        ),
        (
            'agentdebug.diagnose.deep',
            'agentdebug.diagnose.profiles.deepdebug',
            'DeepDebugAnalyzer',
        ),
        (
            'agentdebug.diagnose.attribute.deepdebug',
            'agentdebug.diagnose.profiles.deepdebug',
            'DeepDebugAnalyzer',
        ),
        ('agentdebug.attribution', 'agentdebug.diagnose.attribute', 'HeuristicAttributor'),
        ('agentdebug.recovery', 'agentdebug.diagnose.recover', 'ReflexionSuggestion'),
        ('agentdebug.rules.core', 'agentdebug.diagnose.detect.rules.core', 'KeywordRule'),
        ('agentdebug.ui.server', 'agentdebug.inspect.ui.server', 'build_app'),
    ],
)
def test_legacy_import_paths_resolve_to_canonical_symbols(
    legacy_path: str,
    canonical_path: str,
    symbol: str,
) -> None:
    legacy = importlib.import_module(legacy_path)
    canonical = importlib.import_module(canonical_path)

    assert getattr(legacy, symbol) is getattr(canonical, symbol)


def test_deepdebug_component_loads_canonical_profile() -> None:
    canonical = importlib.import_module('agentdebug.diagnose.profiles.deepdebug')

    assert load_component('attribute.deepdebug') is canonical.DeepDebugAnalyzer
