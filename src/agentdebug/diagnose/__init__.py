"""Diagnosis entry points."""

from agentdebug.core.plugins.registry import register_analysis_plugin
from agentdebug.diagnose.analyzers import HeuristicAnalyzer
from agentdebug.diagnose.detectors import (
    Detector,
    DetectorConfig,
    RepeatedStateDetector,
    RepeatedToolCallDetector,
    StepCountLimitDetector,
    TopicDriftDetector,
    default_detectors,
    run_detectors,
)
from agentdebug.diagnose.judges import LLMJudgeAnalyzer
from agentdebug.diagnose.taxonomy_induction import (
    FailureObservation,
    TaxonomyInducer,
    TaxonomyProposal,
    collect_observations,
)

__all__ = [
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
    'collect_observations',
    'default_detectors',
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
