"""Structured rerun request types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class RerunCapability:
    """Whether the available inputs can support a real rerun."""

    level: str
    executable: bool
    execution_mode: Optional[str] = None
    missing: tuple[str, ...] = ()
    available: tuple[str, ...] = ()
    reason: str = ''


@dataclass(frozen=True)
class RerunCheckpoint:
    """Where a rerun should resume or branch from."""

    event_id: Optional[str] = None
    step_index: Optional[int] = None
    policy: str = 'from_root_cause'


@dataclass(frozen=True)
class RerunDirective:
    """A suggest-only retry instruction produced by recovery."""

    text: str
    source: str = 'diagnosis'
    target_event_id: Optional[str] = None
    requires_human_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerunRequest:
    """Portable artifact consumed by runtime-specific rerun executors."""

    trace_id: str
    checkpoint: RerunCheckpoint
    directive: RerunDirective
    report_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    'RerunCapability',
    'RerunCheckpoint',
    'RerunDirective',
    'RerunRequest',
]
