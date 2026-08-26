"""Shared host capture adapter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Protocol

from agentdebug.capture.contracts import HookNotification, TranscriptSnapshot
from agentdebug.schema import AgentTrajectory


class HostCaptureAdapter(Protocol):
    host: str

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
