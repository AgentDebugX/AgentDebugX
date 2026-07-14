"""Unified registry for Diagnose-stage components.

Detect, Attribute, and Recover components share a single manifest protocol so
CLI surfaces and future plugin discovery can reason about them consistently.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Literal, Optional, cast

from agentdebug.diagnose.detect.rules import list_rule_packs

DiagnoseStage = Literal['detect', 'attribute', 'recover']
_STAGES: tuple[DiagnoseStage, ...] = ('detect', 'attribute', 'recover')
_MANIFEST_ROOT = 'agentdebug.diagnose.component_manifests'


@dataclass(frozen=True)
class DiagnoseComponentMetadata:
    """Manifest metadata for a diagnose-stage component."""

    id: str
    stage: DiagnoseStage
    name: str
    description: str
    entrypoint: str
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    cost_profile: str = 'unknown'
    enabled_by_default: bool = False


def list_components(
    *,
    stage: Optional[DiagnoseStage] = None,
) -> list[DiagnoseComponentMetadata]:
    """Return all built-in Diagnose components, optionally filtered by stage."""

    components = _manifest_components()
    components.extend(_rule_pack_components())
    if stage is not None:
        components = [component for component in components if component.stage == stage]
    return sorted(components, key=lambda component: (component.stage, component.id))


def available_components(*, stage: Optional[DiagnoseStage] = None) -> list[str]:
    """Return component IDs available for a stage or for the full Diagnose loop."""

    return [component.id for component in list_components(stage=stage)]


def get_component_metadata(component_id: str) -> DiagnoseComponentMetadata:
    """Return metadata for one component ID."""

    for component in list_components():
        if component.id == component_id:
            return component
    raise ValueError(
        f'unknown diagnose component {component_id!r}; available: '
        + ', '.join(available_components())
    )


def load_component(component_id: str) -> object:
    """Import and return the component's entrypoint object.

    Entrypoints use ``module:attribute`` for classes/functions. A bare module
    path returns the imported module, which is useful for rule-pack components.
    """

    metadata = get_component_metadata(component_id)
    module_name, sep, attr = metadata.entrypoint.partition(':')
    module = importlib.import_module(module_name)
    if not sep:
        return module
    return getattr(module, attr)


def is_component_available(component_id: str) -> bool:
    """Return whether the entrypoint can be imported in the current environment."""

    try:
        load_component(component_id)
    except Exception:
        return False
    return True


def _manifest_components() -> list[DiagnoseComponentMetadata]:
    components: list[DiagnoseComponentMetadata] = []
    for stage in _STAGES:
        stage_root = files(_MANIFEST_ROOT) / stage
        for manifest in sorted(stage_root.iterdir(), key=lambda path: path.name):
            if not manifest.name.endswith('.json'):
                continue
            payload = json.loads(manifest.read_text(encoding='utf-8'))
            components.append(_metadata_from_mapping(payload))
    return components


def _rule_pack_components() -> list[DiagnoseComponentMetadata]:
    components: list[DiagnoseComponentMetadata] = []
    for pack in list_rule_packs():
        capabilities = ['rule_pack', *pack.capabilities]
        components.append(
            DiagnoseComponentMetadata(
                id=f'detect.rules.{pack.id}',
                stage='detect',
                name=pack.name,
                description=pack.description,
                entrypoint=pack.entrypoint,
                capabilities=_dedupe(capabilities),
                dependencies=list(pack.dependencies),
                cost_profile=pack.cost_profile,
                enabled_by_default=pack.enabled_by_default,
            )
        )
    return components


def _metadata_from_mapping(payload: object) -> DiagnoseComponentMetadata:
    if not isinstance(payload, dict):
        raise ValueError('diagnose component manifest must be a JSON object')
    stage = str(payload['stage'])
    if stage not in _STAGES:
        raise ValueError(f'unknown diagnose component stage: {stage!r}')
    return DiagnoseComponentMetadata(
        id=str(payload['id']),
        stage=cast(DiagnoseStage, stage),
        name=str(payload['name']),
        description=str(payload['description']),
        entrypoint=str(payload['entrypoint']),
        capabilities=[str(item) for item in _sequence(payload.get('capabilities'))],
        dependencies=[str(item) for item in _sequence(payload.get('dependencies'))],
        cost_profile=str(payload.get('cost_profile') or 'unknown'),
        enabled_by_default=bool(payload.get('enabled_by_default')),
    )


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
