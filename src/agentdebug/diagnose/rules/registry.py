"""Rule-pack registry for the heuristic analyzer."""

from __future__ import annotations

from typing import Iterable, List, Sequence, Union

from agentdebug.core.models import AgentTrajectory
from agentdebug.diagnose.rules.base import EventRule, TrajectoryRule

RulePackSpec = Union[str, Sequence[str], None]

_KNOWN_PACKS = ('core', 'agenterrorbench', 'gui')


def available_rule_packs() -> List[str]:
    return list(_KNOWN_PACKS)


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
            for pack in _KNOWN_PACKS:
                _append_unique(resolved, pack)
            continue
        if name not in _KNOWN_PACKS:
            raise ValueError(
                f'unknown rule pack {name!r}; available: {", ".join(_KNOWN_PACKS)}, auto, all'
            )
        _append_unique(resolved, name)

    if not resolved:
        resolved.append('core')
    return resolved


def load_event_rules(rule_packs: Iterable[str]) -> List[EventRule]:
    rules: List[EventRule] = []
    for pack in rule_packs:
        if pack == 'core':
            from agentdebug.diagnose.rules.core import build_event_rules
        elif pack == 'agenterrorbench':
            from agentdebug.diagnose.rules.agenterrorbench import build_event_rules
        elif pack == 'gui':
            from agentdebug.diagnose.rules.gui import build_event_rules
        else:
            raise ValueError(f'unknown rule pack {pack!r}')
        rules.extend(build_event_rules())
    return sorted(rules, key=lambda rule: rule.priority)


def load_trajectory_rules(rule_packs: Iterable[str]) -> List[TrajectoryRule]:
    rules: List[TrajectoryRule] = []
    for pack in rule_packs:
        if pack == 'core':
            from agentdebug.diagnose.rules.core import build_trajectory_rules
        elif pack == 'agenterrorbench':
            from agentdebug.diagnose.rules.agenterrorbench import build_trajectory_rules
        elif pack == 'gui':
            from agentdebug.diagnose.rules.gui import build_trajectory_rules
        else:
            raise ValueError(f'unknown rule pack {pack!r}')
        rules.extend(build_trajectory_rules())
    return sorted(rules, key=lambda rule: rule.priority)


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
