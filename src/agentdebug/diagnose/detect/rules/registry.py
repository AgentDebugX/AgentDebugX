"""Rule-pack registry for the heuristic analyzer."""

from __future__ import annotations

import importlib
import json
from importlib.resources import files
from typing import Iterable, List, Sequence, Union, cast

from agentdebug.schema import AgentTrajectory
from agentdebug.diagnose.detect.rules.base import (
    EventRule,
    RulePackMetadata,
    TrajectoryRule,
)

RulePackSpec = Union[str, Sequence[str], None]

_BUILTIN_PACKS = ('core', 'agenterrorbench', 'gui')
_PACK_ROOT = 'agentdebug.diagnose.detect.rules.packs'


def available_rule_packs() -> List[str]:
    return [metadata.id for metadata in list_rule_packs()]


def list_rule_packs() -> List[RulePackMetadata]:
    """Return manifest metadata for all built-in rule packs."""

    return [_load_rule_pack_metadata(pack_id) for pack_id in _BUILTIN_PACKS]


def get_rule_pack_metadata(pack_id: str) -> RulePackMetadata:
    """Return metadata for one rule pack."""

    if pack_id not in _BUILTIN_PACKS:
        raise ValueError(
            f'unknown rule pack {pack_id!r}; available: {", ".join(_BUILTIN_PACKS)}, auto, all'
        )
    return _load_rule_pack_metadata(pack_id)


def resolve_rule_pack_names(
    rule_packs: RulePackSpec,
    trajectory: AgentTrajectory,
) -> List[str]:
    """Resolve user/default rule-pack settings to concrete pack names."""

    if rule_packs is None:
        requested = ['core']
    elif isinstance(rule_packs, str):
        requested = [rule_packs]
    else:
        requested = list(rule_packs)

    resolved: List[str] = []
    for name in requested:
        if name == 'auto':
            _append_unique(resolved, 'core')
            if _detect_agenterrorbench(trajectory):
                _append_unique(resolved, 'agenterrorbench')
            continue
        if name == 'all':
            for pack in available_rule_packs():
                _append_unique(resolved, pack)
            continue
        if name not in available_rule_packs():
            raise ValueError(
                f'unknown rule pack {name!r}; available: {", ".join(available_rule_packs())}, auto, all'
            )
        _append_unique(resolved, name)

    if not resolved:
        resolved.append('core')
    return resolved


def load_event_rules(rule_packs: Iterable[str]) -> List[EventRule]:
    rules: List[EventRule] = []
    for pack in rule_packs:
        module = _load_rule_pack_module(pack)
        build_event_rules = getattr(module, 'build_event_rules')
        rules.extend(cast(List[EventRule], build_event_rules()))
    return sorted(rules, key=lambda rule: rule.priority)


def load_trajectory_rules(rule_packs: Iterable[str]) -> List[TrajectoryRule]:
    rules: List[TrajectoryRule] = []
    for pack in rule_packs:
        module = _load_rule_pack_module(pack)
        build_trajectory_rules = getattr(module, 'build_trajectory_rules')
        rules.extend(cast(List[TrajectoryRule], build_trajectory_rules()))
    return sorted(rules, key=lambda rule: rule.priority)


def _load_rule_pack_module(pack_id: str) -> object:
    metadata = get_rule_pack_metadata(pack_id)
    return importlib.import_module(metadata.entrypoint)


def _load_rule_pack_metadata(pack_id: str) -> RulePackMetadata:
    manifest_path = files(_PACK_ROOT) / pack_id / 'manifest.json'
    raw = json.loads(manifest_path.read_text(encoding='utf-8'))
    return RulePackMetadata(
        id=str(raw['id']),
        name=str(raw['name']),
        stage=str(raw['stage']),
        description=str(raw['description']),
        entrypoint=str(raw['entrypoint']),
        capabilities=[str(item) for item in raw.get('capabilities', [])],
        dependencies=[str(item) for item in raw.get('dependencies', [])],
        cost_profile=str(raw.get('cost_profile') or 'free'),
        enabled_by_default=bool(raw.get('enabled_by_default')),
    )


def _detect_agenterrorbench(trajectory: AgentTrajectory) -> bool:
    haystack = '\n'.join(
        [
            trajectory.framework or '',
            trajectory.task_id or '',
            str(trajectory.metadata),
        ]
    ).lower()
    return any(
        marker in haystack
        for marker in ('agenterrorbench', 'alfworld', 'webshop')
    )


def _append_unique(items: List[str], item: str) -> None:
    if item not in items:
        items.append(item)
