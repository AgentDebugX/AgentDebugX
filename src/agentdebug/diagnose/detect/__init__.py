"""Detect-stage APIs.

Detection identifies observable failures and labels them with structured
failure modes. It does not by itself decide the responsible upstream cause.
"""

from agentdebug.diagnose.detect.analyzers import HeuristicAnalyzer
from agentdebug.diagnose.detect.detectors import (
    Detector,
    DetectorConfig,
    RepeatedStateDetector,
    RepeatedToolCallDetector,
    StepCountLimitDetector,
    TopicDriftDetector,
    default_detectors,
    run_detectors,
)
from agentdebug.diagnose.detect.judge import LLMJudgeAnalyzer
from agentdebug.diagnose.detect.taxonomy_induction import (
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
