"""Shared host capture adapter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol

from agentdebug.capture.context import CurrentCaptureContext
from agentdebug.capture.contracts import (
    CaptureResult,
    HookNotification,
    TranscriptSnapshot,
)
from agentdebug.schema import AgentTrajectory


class HostCaptureAdapter(Protocol):
    host: str
    event_boundaries: Mapping[str, str]

    def parse_notification(self, payload: Dict[str, Any]) -> HookNotification:
        ...

    def validate_transcript_path(self, notification: HookNotification) -> Path:
        ...

    def normalize(
        self,
        notification: HookNotification,
        snapshot: TranscriptSnapshot,
        trace_id: str,
    ) -> AgentTrajectory:
        ...


class CaptureHost(Protocol):
    cli_name: str
    host_name: str
    event_boundaries: Mapping[str, str]

    def create_adapter(self) -> HostCaptureAdapter:
        ...

    def settings_path(self, project_root: Path) -> Path:
        ...

    def after_dispatch(
        self,
        project_root: Path,
        notification: HookNotification,
        result: CaptureResult,
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        ...

    def resolve_current_context(
        self,
        environ: Mapping[str, str],
        cwd: Path,
    ) -> Optional[CurrentCaptureContext]:
        ...
