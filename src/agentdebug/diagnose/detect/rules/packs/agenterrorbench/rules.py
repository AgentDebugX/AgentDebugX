"""AgentErrorBench-specific event rules.

These rules are intentionally not part of the always-on core pack. Benchmark
environments can use phrases that are weak or domain-specific in normal traces
but meaningful inside AgentErrorBench-style ALFWorld/WebShop rollouts.
"""

from __future__ import annotations

from typing import List, Optional

from agentdebug.schema import AgentEvent, AgentTrajectory
from agentdebug.diagnose.detect.rules.base import RuleMatch


class AlfworldNoOpRule:
    rule_id = 'agenterrorbench.alfworld.nothing_happens'
    rule_pack = 'agenterrorbench'
    priority = 50

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        if 'nothing happens' not in text.lower():
            return None
        if not _looks_alfworld(trajectory):
            return None
        return RuleMatch(
            mode_id='planning.inefficient_plan',
            evidence=['ALFWorld environment returned "Nothing happens"'],
            rule_id=self.rule_id,
            rule_pack=self.rule_pack,
            confidence_basis='ALFWorld no-op observation is a weak no-progress signal',
            priority=self.priority,
            metadata={'benchmark': 'agenterrorbench', 'environment': 'alfworld'},
        )


class WebShopNoProgressRule:
    rule_id = 'agenterrorbench.webshop.no_progress'
    rule_pack = 'agenterrorbench'
    priority = 60

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        lowered = text.lower()
        if not _looks_webshop(trajectory):
            return None
        if not any(signal in lowered for signal in ('no results', 'not available', 'not found')):
            return None
        return RuleMatch(
            mode_id='planning.inefficient_plan',
            evidence=['WebShop page content suggests no progress toward target item'],
            rule_id=self.rule_id,
            rule_pack=self.rule_pack,
            confidence_basis='WebShop search/detail page contains weak no-progress signal',
            priority=self.priority,
            metadata={'benchmark': 'agenterrorbench', 'environment': 'webshop'},
        )


def build_event_rules() -> List[object]:
    return [
        AlfworldNoOpRule(),
        WebShopNoProgressRule(),
    ]


def build_trajectory_rules() -> List[object]:
    return []


def build_rules() -> List[object]:
    """Backward-compatible alias for the event-rule builder."""

    return build_event_rules()


def _looks_alfworld(trajectory: AgentTrajectory) -> bool:
    haystack = _trajectory_haystack(trajectory)
    return 'alfworld' in haystack


def _looks_webshop(trajectory: AgentTrajectory) -> bool:
    haystack = _trajectory_haystack(trajectory)
    return 'webshop' in haystack


def _trajectory_haystack(trajectory: AgentTrajectory) -> str:
    values = [
        trajectory.framework or '',
        trajectory.task_id or '',
        str(trajectory.metadata),
    ]
    return '\n'.join(values).lower()
