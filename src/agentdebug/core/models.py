"""Core data models for portable agent debugging traces."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

JsonDict = Dict[str, Any]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Create a short stable identifier for trace objects."""
    return f'{prefix}_{uuid4().hex}'


class EventType(str, Enum):
    RUN_START = 'run.start'
    RUN_END = 'run.end'
    AGENT_STEP = 'agent.step'
    LLM_CALL = 'llm.call'
    LLM_RESPONSE = 'llm.response'
    TOOL_CALL = 'tool.call'
    TOOL_RESULT = 'tool.result'
    MEMORY_READ = 'memory.read'
    MEMORY_WRITE = 'memory.write'
    REFLECTION = 'reflection'
    PLAN = 'plan'
    HANDOFF = 'handoff'
    GUARDRAIL = 'guardrail'
    OBSERVATION = 'observation'
    ERROR = 'error'
    HUMAN_FEEDBACK = 'human.feedback'


class Modality(str, Enum):
    TEXT = 'text'
    IMAGE = 'image'
    AUDIO = 'audio'
    VIDEO = 'video'
    UI = 'ui'
    FILE = 'file'
    TOOL = 'tool'
    STATE = 'state'
    OTHER = 'other'


class Artifact(BaseModel):
    """A payload linked to an event, including multimodal evidence."""

    model_config = ConfigDict(use_enum_values=True)

    uri: str
    modality: Modality = Modality.OTHER
    media_type: Optional[str] = None
    description: Optional[str] = None
    metadata: JsonDict = Field(default_factory=dict)


class AgentEvent(BaseModel):
    """A normalized event emitted by an agentic system."""

    model_config = ConfigDict(use_enum_values=True)

    event_id: str = Field(default_factory=lambda: new_id('evt'))
    trace_id: str
    parent_event_id: Optional[str] = None
    agent_name: str = 'agent'
    event_type: EventType = EventType.AGENT_STEP
    module: Optional[str] = None
    step_index: Optional[int] = None
    timestamp: datetime = Field(default_factory=utc_now)
    input: Any = None
    output: Any = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: JsonDict = Field(default_factory=dict)
    artifacts: List[Artifact] = Field(default_factory=list)


class AgentTrajectory(BaseModel):
    """Portable trajectory IR for single-agent and multi-agent runs."""

    trace_id: str = Field(default_factory=lambda: new_id('trace'))
    task_id: Optional[str] = None
    goal: Optional[str] = None
    framework: Optional[str] = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: Optional[datetime] = None
    metadata: JsonDict = Field(default_factory=dict)
    events: List[AgentEvent] = Field(default_factory=list)

    def add_event(self, event: AgentEvent) -> AgentEvent:
        self.events.append(event)
        return event

    def prefix(self, n: int) -> 'AgentTrajectory':
        """Return a copy keeping only the first ``n`` events.

        Used by replay/attribution backends (Binary-Search, Delta-Debugging)
        that need to ask "would the trajectory still fail if it had stopped
        at step k?" The returned object is a SHALLOW copy of the events list
        but a fresh AgentTrajectory; mutating it does not touch the original.
        """
        truncated = self.model_copy(deep=False) if hasattr(self, 'model_copy') else self.copy(deep=False)
        truncated.events = list(self.events[:max(0, n)])
        return truncated


class FailureMode(BaseModel):
    """A seed or generated taxonomy node."""

    mode_id: str
    name: str
    family: str
    description: str
    signals: List[str] = Field(default_factory=list)
    suggestion_templates: List[str] = Field(default_factory=list)
    source: Optional[str] = None


class FailureFinding(BaseModel):
    """A localized failure diagnosis for one event or trajectory region."""

    finding_id: str = Field(default_factory=lambda: new_id('finding'))
    failure_mode: FailureMode
    event_id: Optional[str] = None
    agent_name: Optional[str] = None
    step_index: Optional[int] = None
    confidence: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)
    suggestion: Optional[str] = None
    metadata: JsonDict = Field(default_factory=dict)


def confidence_or_default(value: Optional[float], default: float = 0.0) -> float:
    """Return a numeric confidence fallback for legacy ranking code."""

    return default if value is None else value


class DiagnosticReport(BaseModel):
    """Structured output from an analyzer."""

    report_id: str = Field(default_factory=lambda: new_id('report'))
    trace_id: str
    task_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=utc_now)
    root_cause_event_id: Optional[str] = None
    root_cause_agent: Optional[str] = None
    root_cause_step_index: Optional[int] = None
    findings: List[FailureFinding] = Field(default_factory=list)
    summary: str = 'No failure was detected.'
    suggestions: List[str] = Field(default_factory=list)
    attribution: Optional[JsonDict] = None
    recovery: Optional[JsonDict] = None
    metadata: JsonDict = Field(default_factory=dict)


def model_to_json(model: BaseModel, indent: Optional[int] = None) -> str:
    """Serialize a Pydantic model across v1 and v2 runtimes."""
    dumper = getattr(model, 'model_dump_json', None)
    if callable(dumper):
        return str(dumper(indent=indent))
    return str(model.json(indent=indent))


def trajectory_from_json(payload: str) -> AgentTrajectory:
    """Parse an AgentTrajectory across Pydantic v1 and v2 runtimes."""
    validator = getattr(AgentTrajectory, 'model_validate_json', None)
    if callable(validator):
        return cast(AgentTrajectory, validator(payload))
    return AgentTrajectory.parse_raw(payload)


def report_from_json(payload: str) -> DiagnosticReport:
    """Parse a DiagnosticReport across Pydantic v1 and v2 runtimes."""
    validator = getattr(DiagnosticReport, 'model_validate_json', None)
    if callable(validator):
        return cast(DiagnosticReport, validator(payload))
    return DiagnosticReport.parse_raw(payload)
