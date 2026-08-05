"""Attribute-stage APIs.

Attribution traces observed failures back to the responsible step or agent.
"""

from agentdebug.diagnose.attribute.async_api import (
    attribute_async,
    attribute_many_async,
    supports_native_async,
)
from agentdebug.diagnose.attribute.attribution import (
    AllAtOnceAttributor,
    AttributionBudget,
    AttributionResult,
    Attributor,
    BinarySearchAttributor,
    Blame,
    CounterfactualAttributor,
    EnsembleAttributor,
    HeuristicAttributor,
    SBFLAttributor,
    StepByStepAttributor,
)
from agentdebug.diagnose.profiles.deepdebug import (
    DeepDebugAnalyzer,
    DeepDebugResult,
    DeepDebugRound,
)

__all__ = [
    'AllAtOnceAttributor',
    'AttributionBudget',
    'AttributionResult',
    'Attributor',
    'BinarySearchAttributor',
    'Blame',
    'CounterfactualAttributor',
    'DeepDebugAnalyzer',
    'DeepDebugResult',
    'DeepDebugRound',
    'EnsembleAttributor',
    'HeuristicAttributor',
    'SBFLAttributor',
    'StepByStepAttributor',
    'attribute_async',
    'attribute_many_async',
    'supports_native_async',
]
