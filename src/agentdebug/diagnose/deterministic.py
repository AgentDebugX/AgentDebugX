"""Compatibility facade for deterministic diagnosis."""

from agentdebug.diagnose.detect import HeuristicAnalyzer
from agentdebug.diagnose.detect import (
    Detector,
    DetectorConfig,
    RepeatedStateDetector,
    RepeatedToolCallDetector,
    StepCountLimitDetector,
    TopicDriftDetector,
    default_detectors,
    run_detectors,
)
from agentdebug.diagnose.detect.rules import (
    EventRule,
    RuleMatch,
    TrajectoryRule,
    available_rule_packs,
    load_event_rules,
    load_trajectory_rules,
    resolve_rule_pack_names,
)

__all__ = [
    'Detector',
    'DetectorConfig',
    'EventRule',
    'HeuristicAnalyzer',
    'RepeatedStateDetector',
    'RepeatedToolCallDetector',
    'RuleMatch',
    'StepCountLimitDetector',
    'TopicDriftDetector',
    'TrajectoryRule',
    'available_rule_packs',
    'default_detectors',
    'load_event_rules',
    'load_trajectory_rules',
    'resolve_rule_pack_names',
    'run_detectors',
]
