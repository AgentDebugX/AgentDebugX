"""Core AgentDebugX primitives."""

from agentdebug.core.events import DEFAULT_BUS, BusEvent, EventBus, EventSubscription
from agentdebug.core.llm import (
    CompletionResult,
    EmbeddingClient,
    LLMClient,
    OpenAICompatClient,
    extract_json_block,
)
from agentdebug.core.models import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
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
from agentdebug.core.storage import JsonlTraceStore, SQLiteTraceStore, TraceStore
from agentdebug.core.taxonomy import SEED_FAILURE_MODES, get_failure_mode

__all__ = [
    'AgentEvent',
    'AgentTrajectory',
    'Artifact',
    'BusEvent',
    'CompletionResult',
    'DEFAULT_BUS',
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
