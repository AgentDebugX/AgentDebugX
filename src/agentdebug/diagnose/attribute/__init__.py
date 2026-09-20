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
    CorrectedAction,
    CounterfactualAttributor,
    EnsembleAttributor,
    AttributionUnavailable,
    HeuristicAttributor,
    NO_FALLBACK,
    NoFallback,
    SBFLAttributor,
    StepByStepAttributor,
)
from agentdebug.diagnose.attribute.moa import (
    MixtureOfAgentsAttributor,
    MixtureOfAgentsDiagnosis,
    Proposal,
    ProposerSpec,
    SeededClient,
    SummaryProvenance,
    proposal_sort_key,
    proposers_from_clients,
    proposers_from_seeds,
)
from agentdebug.diagnose.profiles.deepdebug import (
    DeepDebugAnalyzer,
    DeepDebugResult,
    DeepDebugRound,
)

__all__ = [
    'AllAtOnceAttributor',
    'ReferenceAttributor',
    'AttributionBudget',
    'AttributionResult',
    'Attributor',
    'BinarySearchAttributor',
    'Blame',
    'CorrectedAction',
    'CounterfactualAttributor',
    'DeepDebugAnalyzer',
    'DeepDebugResult',
    'DeepDebugRound',
    'EnsembleAttributor',
    'AttributionUnavailable',
    'HeuristicAttributor',
    'MixtureOfAgentsAttributor',
    'MixtureOfAgentsDiagnosis',
    'NO_FALLBACK',
    'NoFallback',
    'Proposal',
    'ProposerSpec',
    'SBFLAttributor',
    'SeededClient',
    'StepByStepAttributor',
    'SummaryProvenance',
    'attribute_async',
    'attribute_many_async',
    'proposal_sort_key',
    'proposers_from_clients',
    'proposers_from_seeds',
    'supports_native_async',
]
from .reference import ReferenceAttributor  # noqa: E402,F401
