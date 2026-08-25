"""Serializable contracts for one AgentDebugX debug run."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from agentdebug.schema.models import new_id, utc_now

RunStatus = Literal['planned', 'running', 'completed', 'partial', 'failed']


class ConfigurationSource(BaseModel):
    value: str
    source: Literal['default', 'profile', 'config', 'override']


class ResolvedPipeline(BaseModel):
    profile: str
    input_format: ConfigurationSource
    diagnoser: ConfigurationSource
    attributor: ConfigurationSource
    recovery: ConfigurationSource
    llm_required: bool = False


class RunInput(BaseModel):
    reference: str
    detected_format: Optional[str] = None


class RunArtifactRefs(BaseModel):
    trace_id: Optional[str] = None
    report_id: Optional[str] = None
    store_type: str
    store_path: str


class RunIssue(BaseModel):
    code: str
    message: str
    phase: Optional[str] = None


class RunWarning(RunIssue):
    """A non-fatal phase warning, such as optional UI startup failure."""


class RunError(RunIssue):
    """A structured failure that prevented all or part of the run."""


class RunAction(BaseModel):
    action: str
    description: str


class DebugRun(BaseModel):
    schema_version: int = 1
    run_id: str = Field(default_factory=lambda: new_id('dbg'))
    status: RunStatus
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    input: RunInput
    requested_profile: str
    resolved_pipeline: ResolvedPipeline
    artifacts: RunArtifactRefs
    candidate_root_cause: Optional[Dict[str, Any]] = None
    top_evidence: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[RunWarning] = Field(default_factory=list)
    errors: List[RunError] = Field(default_factory=list)
    actions: List[RunAction] = Field(default_factory=list)
    ui_url: Optional[str] = None


class RunRequest(BaseModel):
    input_reference: str
    profile: str = 'standard'
    format_override: Optional[str] = None
    diagnoser_override: Optional[str] = None
    attributor_override: Optional[str] = None
    recovery_override: Optional[str] = None
    store_type: Literal['sqlite', 'jsonl'] = 'sqlite'
    store_path: str = '.agentdebug/agentdebug.sqlite'
    run_root: str = '.agentdebug'
    plan_only: bool = False
    ui: bool = False


class RunResult(BaseModel):
    schema_version: int = 1
    run_id: str
    status: str
    trace_id: Optional[str] = None
    report_id: Optional[str] = None
    candidate_root_cause: Optional[Dict[str, Any]] = None
    top_evidence: List[str] = Field(default_factory=list)
    resolved_pipeline: ResolvedPipeline
    actions: List[str] = Field(default_factory=list)
    ui_url: Optional[str] = None
    warnings: List[RunWarning] = Field(default_factory=list)
    errors: List[RunError] = Field(default_factory=list)

    @classmethod
    def from_run(cls, run: DebugRun) -> 'RunResult':
        return cls(
            run_id=run.run_id, status=run.status,
            trace_id=run.artifacts.trace_id, report_id=run.artifacts.report_id,
            candidate_root_cause=run.candidate_root_cause,
            top_evidence=run.top_evidence, resolved_pipeline=run.resolved_pipeline,
            actions=[item.action for item in run.actions], ui_url=run.ui_url,
            warnings=run.warnings, errors=run.errors,
        )
