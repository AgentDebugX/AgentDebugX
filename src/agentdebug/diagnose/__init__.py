"""Diagnosis entry points."""

from agentdebug.runtime.plugins.registry import register_analysis_plugin
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
from agentdebug.diagnose.detect import LLMJudgeAnalyzer
from agentdebug.diagnose.detect import (
    FailureObservation,
    TaxonomyInducer,
    TaxonomyProposal,
    collect_observations,
)
from agentdebug.diagnose.registry import (
    DiagnoseComponentMetadata,
    available_components,
    get_component_metadata,
    is_component_available,
    list_components,
    load_component,
)

__all__ = [
    'DiagnoseComponentMetadata',
    'Detector',
    'DetectorConfig',
    'FailureObservation',
    'HeuristicAnalyzer',
    'LLMJudgeAnalyzer',
    'RepeatedStateDetector',
    'RepeatedToolCallDetector',
    'StepCountLimitDetector',
    'TaxonomyInducer',
    'TaxonomyProposal',
    'TopicDriftDetector',
    'available_components',
    'collect_observations',
    'default_detectors',
    'get_component_metadata',
    'is_component_available',
    'list_components',
    'load_component',
    'run_detectors',
]

register_analysis_plugin(
    'analysis.heuristic',
    'Deterministic Analysis',
    version='1',
    capabilities=['diagnose', 'findings', 'root_cause'],
    source_module='agentdebug.analyzers',
)
register_analysis_plugin(
    'analysis.judge.llm',
    'LLM Judge Analysis',
    version='1',
    capabilities=['diagnose', 'llm_judge'],
    source_module='agentdebug.judges',
)
register_analysis_plugin(
    'analysis.deep',
    'Deep Diagnosis',
    version='1',
    capabilities=['diagnose', 'multi_round_analysis'],
    source_module='agentdebug.deep',
)
