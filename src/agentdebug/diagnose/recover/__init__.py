"""Recover-stage APIs.

Recovery turns a localized diagnosis into suggest-only retry guidance.
"""

from agentdebug.diagnose.recover.recovery import (
    AutoManualRules,
    CompensationSpec,
    Compensator,
    CriticRecoverer,
    DEFAULT_VERIFIERS,
    DeepDebugRecovery,
    FixProposal,
    Recoverer,
    ReflexionSuggestion,
    SagaRollback,
    SelfRefineLoop,
    VerifierSpec,
)

__all__ = [
    'AutoManualRules',
    'CompensationSpec',
    'Compensator',
    'CriticRecoverer',
    'DEFAULT_VERIFIERS',
    'DeepDebugRecovery',
    'FixProposal',
    'Recoverer',
    'ReflexionSuggestion',
    'SagaRollback',
    'SelfRefineLoop',
    'VerifierSpec',
]
