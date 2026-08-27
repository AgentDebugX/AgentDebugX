"""Detect-stage APIs.

Detection identifies observable failures and labels them with structured
failure modes. It does not by itself decide the responsible upstream cause.
"""

from agentdebug.diagnose.detect.analyzers import HeuristicAnalyzer
from agentdebug.diagnose.detect.compression import (
    GradedContextBuilder,
    StepCompressor,
    clip_middle,
    render_history_for_focus,
)
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
from agentdebug.diagnose.detect.selection import (
    SELECTORS,
    RootSelector,
    earliest_finding,
    get_selector,
    most_confident_finding,
)
from agentdebug.diagnose.detect.taxonomy_induction import (
    FailureObservation,
    TaxonomyInducer,
    TaxonomyProposal,
    collect_observations,
)

__all__ = [
    'SELECTORS',
    'Detector',
    'DetectorConfig',
    'FailureObservation',
    'GradedContextBuilder',
    'HeuristicAnalyzer',
    'LLMJudgeAnalyzer',
    'RepeatedStateDetector',
    'RepeatedToolCallDetector',
    'RootSelector',
    'StepCompressor',
    'StepCountLimitDetector',
    'TaxonomyInducer',
    'TaxonomyProposal',
    'TopicDriftDetector',
    'clip_middle',
    'collect_observations',
    'default_detectors',
    'earliest_finding',
    'get_selector',
    'most_confident_finding',
    'render_history_for_focus',
    'run_detectors',
]
