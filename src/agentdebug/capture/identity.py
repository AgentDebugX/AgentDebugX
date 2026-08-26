"""Deterministic identities for cumulative transcript capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from agentdebug.capture.contracts import (
    CaptureRequest,
    HookNotification,
    TranscriptSnapshot,
)


def _digest(prefix: str, parts: Iterable[str]) -> str:
    payload = b'\0'.join(part.encode('utf-8') for part in (prefix, *parts))
    return hashlib.sha256(payload).hexdigest()


def trace_id_for(host: str, session_id: str) -> str:
    return f'capture_{_digest("trace", (host, session_id))[:32]}'


def project_id_for(project_root: Path) -> str:
    root = str(project_root.expanduser().resolve())
    return f'project_{_digest("project", (root,))[:32]}'


def receipt_id_for(notification: HookNotification, source_version: str) -> str:
    payload = json.dumps(
        notification.native_payload,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return f'receipt_{_digest("receipt", (
        notification.host,
        notification.session_id,
        notification.event_name,
        notification.native_event_id or '',
        payload,
        source_version,
    ))}'


def boundary_id_for(
    request: CaptureRequest, snapshot: TranscriptSnapshot
) -> str:
    notification = request.notification
    return f'boundary_{_digest("boundary", (
        notification.host,
        notification.session_id,
        request.logical_boundary_kind,
        notification.native_event_id or '',
        snapshot.last_record_sha256,
    ))}'


def event_id_for(
    host: str, session_id: str, native_parts: tuple[str, ...]
) -> str:
    return f'event_{_digest("event", (host, session_id, *native_parts))}'
