"""Rule packs for deterministic analysis.

Rules are split out of ``HeuristicAnalyzer`` so projects can load only the
generic baseline or opt into benchmark/framework-specific signals. A pack can
provide both single-event rules and cross-event trajectory rules.
"""

from agentdebug.runtime.plugins.registry import register_analysis_plugin
from agentdebug.diagnose.detect.rules.base import (
    EventRule,
    RuleMatch,
    RulePackMetadata,
    TrajectoryRule,
)
from agentdebug.diagnose.detect.rules.registry import (
    available_rule_packs,
    get_rule_pack_metadata,
    list_rule_packs,
    load_event_rules,
    load_trajectory_rules,
    resolve_rule_pack_names,
)

__all__ = [
    'EventRule',
    'RuleMatch',
    'RulePackMetadata',
    'TrajectoryRule',
    'available_rule_packs',
    'get_rule_pack_metadata',
    'list_rule_packs',
    'load_event_rules',
    'load_trajectory_rules',
    'resolve_rule_pack_names',
]

register_analysis_plugin(
    'analysis.rule_pack.core',
    'Core Rule Pack',
    version='1',
    capabilities=['single_event_rules', 'cross_event_rules'],
    source_module='agentdebug.diagnose.detect.rules.core',
)
register_analysis_plugin(
    'analysis.rule_pack.agenterrorbench',
    'AgentErrorBench Rule Pack',
    version='1',
    capabilities=['single_event_rules', 'benchmark_overrides'],
    source_module='agentdebug.diagnose.detect.rules.agenterrorbench',
)
register_analysis_plugin(
    'analysis.rule_pack.gui',
    'GUI Taxonomy Rule Pack',
    version='1',
    capabilities=['taxonomy_modes'],
    source_module='agentdebug.diagnose.detect.rules.gui',
)
