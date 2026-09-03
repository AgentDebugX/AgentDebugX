"""Host-neutral contracts for automatic trajectory capture."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from agentdebug.schema import AgentTrajectory

HostName = Literal['claude_code', 'codex']
CaptureStatusName = Literal['disabled', 'no_op', 'captured', 'pending', 'failed']
ReceiptStatusName = Literal['pending', 'committed', 'no_op', 'failed']


class HookNotification(BaseModel):
    schema_version: int = 1
    host: HostName
    event_name: str
    session_id: str
    transcript_path: Path
    cwd: Path
    observed_at: datetime
    native_event_id: Optional[str] = None
    task: Optional[Dict[str, Any]] = None
    session_end_reason: Optional[str] = None
    native_payload: Dict[str, Any] = Field(default_factory=dict)


class TranscriptSnapshot(BaseModel):
    path: Path
    complete_bytes: bytes
    complete_size: int
    content_sha256: str
    last_record_sha256: str
    records: List[Dict[str, Any]] = Field(default_factory=list)
    ignored_tail_bytes: int = 0


class CaptureRequest(BaseModel):
    notification: HookNotification
    project_id: str
    trace_id: str
    receipt_id: str
    logical_boundary_kind: str
    source_version: str


class CaptureResult(BaseModel):
    status: CaptureStatusName
    trace_id: Optional[str] = None
    event_count: int = 0
    last_event_id: Optional[str] = None
    boundary_id: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class PreparedTrajectory(BaseModel):
    trajectory: AgentTrajectory
    counters: Dict[str, int] = Field(default_factory=dict)


class CaptureReceipt(BaseModel):
    receipt_id: str
    host: str
    session_id: str
    project_id: str
    transcript_path: Path
    cwd: Path
    native_event_name: str
    logical_boundary_kind: str
    boundary_id: Optional[str] = None
    native_event_id: Optional[str] = None
    observed_at: datetime
    transcript_size: Optional[int] = None
    status: ReceiptStatusName
    task: Optional[Dict[str, Any]] = None
    session_end_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    native_payload: Dict[str, Any] = Field(default_factory=dict)
    source_version: str = ''


class CaptureSession(BaseModel):
    host: str
    session_id: str
    project_id: str
    trace_id: str
    transcript_path: Path
    transcript_size: int = 0
    transcript_sha256: Optional[str] = None
    last_boundary_id: Optional[str] = None
    last_event_id: Optional[str] = None
    event_count: int = 0
    status: str
    adapter_version: int = 1
    updated_at: datetime
    ended_at: Optional[datetime] = None


class CaptureRepositoryStatus(BaseModel):
    project_id: str
    sessions: List[CaptureSession] = Field(default_factory=list)
    pending_receipts: int = 0
    failed_receipts: int = 0
    committed_receipts: int = 0
