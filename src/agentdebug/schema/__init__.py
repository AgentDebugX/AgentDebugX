"""Portable data contracts for AgentDebugX.

This namespace is the stable home for the framework-independent artifacts
described in the paper: trajectories, events, findings, reports, and failure
taxonomies. The legacy ``agentdebug.core`` modules remain import-compatible.
"""

from agentdebug.schema.models import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    DiagnosticAuditEntry,
    DiagnosticReport,
    EventType,
    ConflictAxis,
    FailureFinding,
    FailureMode,
    Modality,
    JsonDict,
    confidence_or_default,
    model_to_dict,
    model_to_json,
    new_id,
    report_from_json,
    trajectory_from_json,
    utc_now,
)
from agentdebug.schema.taxonomy import SEED_FAILURE_MODES, get_failure_mode

__all__ = [
    'AgentEvent',
    'AgentTrajectory',
    'Artifact',
    'DiagnosticAuditEntry',
    'DiagnosticReport',
    'EventType',
    'ConflictAxis',
    'FailureFinding',
    'FailureMode',
    'JsonDict',
    'Modality',
    'SEED_FAILURE_MODES',
    'confidence_or_default',
    'get_failure_mode',
    'model_to_dict',
    'model_to_json',
    'new_id',
    'report_from_json',
    'trajectory_from_json',
    'utc_now',
]
