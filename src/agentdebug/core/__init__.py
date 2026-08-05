"""Core AgentDebugX primitives."""

"""Backward-compatible aggregate import path for schema/runtime APIs."""

from agentdebug.runtime.events import DEFAULT_BUS, BusEvent, EventBus, EventSubscription
from agentdebug.runtime.llm import (
    CompletionResult,
    TokenUsage,
    EmbeddingClient,
    LLMClient,
    OpenAICompatClient,
    extract_json_block,
)
from agentdebug.schema.models import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    DiagnosticAuditEntry,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
    JsonDict,
    Modality,
    confidence_or_default,
    model_to_json,
    new_id,
    report_from_json,
    trajectory_from_json,
    utc_now,
)
from agentdebug.runtime.storage import JsonlTraceStore, SQLiteTraceStore, TraceStore
from agentdebug.schema.taxonomy import SEED_FAILURE_MODES, get_failure_mode

__all__ = [
    'AgentEvent',
    'AgentTrajectory',
    'Artifact',
    'BusEvent',
    'CompletionResult',
    'TokenUsage',
    'DEFAULT_BUS',
    'DiagnosticAuditEntry',
    'DiagnosticReport',
    'EmbeddingClient',
    'EventBus',
    'EventSubscription',
    'EventType',
    'FailureFinding',
    'FailureMode',
    'JsonDict',
    'JsonlTraceStore',
    'LLMClient',
    'Modality',
    'OpenAICompatClient',
    'SEED_FAILURE_MODES',
    'SQLiteTraceStore',
    'TraceStore',
    'confidence_or_default',
    'extract_json_block',
    'get_failure_mode',
    'model_to_json',
    'new_id',
    'report_from_json',
    'trajectory_from_json',
    'utc_now',
]
