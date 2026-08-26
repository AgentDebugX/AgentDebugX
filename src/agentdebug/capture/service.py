"""Capture-only orchestration for passive host hook notifications."""

from __future__ import annotations

import time
import sqlite3
from pathlib import Path
from typing import Callable

from agentdebug.capture.config import load_capture_config
from agentdebug.capture.contracts import CaptureRequest, CaptureResult, HookNotification
from agentdebug.capture.filtering import prepare_for_capture
from agentdebug.capture.hosts.base import HostCaptureAdapter
from agentdebug.capture.identity import (
    boundary_id_for,
    project_id_for,
    receipt_id_for,
    trace_id_for,
)
from agentdebug.capture.repository import CaptureRepository
from agentdebug.capture.snapshot import read_complete_jsonl

BOUNDARY_KINDS = {
    'SessionStart': 'session_start_reconciled',
    'UserPromptSubmit': 'pre_prompt_reconciled',
    'Stop': 'turn_completed',
    'TaskCompleted': 'task_completion_requested',
    'SessionEnd': 'session_reconciled',
    'after_agent': 'turn_completed',
}


class CaptureService:
    def __init__(
        self,
        project_root: Path,
        adapter: HostCaptureAdapter,
        *,
        repository_factory: Callable[[Path], CaptureRepository] = CaptureRepository,
        session_end_deadline_ms: float = 800.0,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.adapter = adapter
        self.repository_factory = repository_factory
        self.session_end_deadline_ms = session_end_deadline_ms

    def handle(self, notification: HookNotification) -> CaptureResult:
        started = time.perf_counter()
        config = load_capture_config(self.project_root)
        platform_config = (
            None if config is None else config.platforms.get(notification.host)
        )
        if platform_config is None or not platform_config.enabled:
            return CaptureResult(
                status='disabled', elapsed_ms=_elapsed_ms(started)
            )
        receipt_id = None
        repository = None
        try:
            if config.project_root.expanduser().resolve() != self.project_root:
                raise ValueError('capture config project root does not match dispatch scope')
            if self.adapter.host != notification.host:
                raise ValueError('capture platform does not match hook notification')
            cwd = notification.cwd.expanduser().resolve()
            try:
                cwd.relative_to(self.project_root)
            except ValueError as exc:
                raise ValueError('hook cwd is outside the configured project') from exc
            logical_boundary = BOUNDARY_KINDS.get(notification.event_name)
            if logical_boundary is None:
                raise ValueError(f'unsupported capture event: {notification.event_name}')
            transcript_path = self.adapter.validate_transcript_path(notification)
            stat = transcript_path.stat()
            source_version = (
                f'{transcript_path}\0{stat.st_size}\0{stat.st_mtime_ns}'
            )
            trace_id = trace_id_for(notification.host, notification.session_id)
            project_id = project_id_for(self.project_root)
            repository = self.repository_factory(config.store_path)
            session = repository.load_session(
                notification.host, notification.session_id
            )
            replayable_for_session = any(
                receipt.host == notification.host
                and receipt.session_id == notification.session_id
                for receipt in repository.list_replayable(project_id)
            )

            reconciliation = notification.event_name in {
                'SessionStart',
                'UserPromptSubmit',
            }
            if (
                notification.event_name == 'SessionStart'
                and session is None
                and not replayable_for_session
            ):
                return CaptureResult(
                    status='no_op', trace_id=trace_id, elapsed_ms=_elapsed_ms(started)
                )
            if (
                reconciliation
                and session is not None
                and session.transcript_size == stat.st_size
                and not replayable_for_session
            ):
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    event_count=session.event_count,
                    last_event_id=session.last_event_id,
                    boundary_id=session.last_boundary_id,
                    elapsed_ms=_elapsed_ms(started),
                )

            receipt_id = receipt_id_for(notification, source_version)
            request = CaptureRequest(
                notification=notification,
                project_id=project_id,
                trace_id=trace_id,
                receipt_id=receipt_id,
                logical_boundary_kind=logical_boundary,
                source_version=source_version,
            )
            receipt = repository.begin_receipt(request)
            if receipt.status in {'committed', 'no_op'}:
                current = repository.load_session(
                    notification.host, notification.session_id
                )
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    event_count=0 if current is None else current.event_count,
                    last_event_id=None if current is None else current.last_event_id,
                    boundary_id=receipt.boundary_id,
                    elapsed_ms=_elapsed_ms(started),
                )

            snapshot = read_complete_jsonl(transcript_path)
            if (
                notification.event_name == 'SessionEnd'
                and _elapsed_ms(started) >= self.session_end_deadline_ms
            ):
                return CaptureResult(
                    status='pending',
                    trace_id=trace_id,
                    elapsed_ms=_elapsed_ms(started),
                )
            boundary_id = boundary_id_for(request, snapshot)
            existing_boundary = repository.load_boundary_receipt(
                notification.host, notification.session_id, boundary_id
            )
            if (
                existing_boundary is not None
                and existing_boundary.receipt_id != receipt_id
            ):
                repository.commit_no_op(
                    receipt_id,
                    snapshot,
                    {
                        'boundary_id': None,
                        'warnings': ['duplicate content boundary'],
                        'duration_ms': _elapsed_ms(started),
                    },
                )
                current = repository.load_session(
                    notification.host, notification.session_id
                )
                repository.reconcile_prior_receipts(
                    project_id,
                    notification.host,
                    notification.session_id,
                    current_receipt_id=receipt_id,
                )
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    event_count=0 if current is None else current.event_count,
                    last_event_id=None if current is None else current.last_event_id,
                    boundary_id=boundary_id,
                    warnings=['duplicate content boundary'],
                    elapsed_ms=_elapsed_ms(started),
                )
            if session is not None and session.transcript_sha256 == snapshot.content_sha256:
                repository.commit_no_op(
                    receipt_id,
                    snapshot,
                    {
                        'boundary_id': boundary_id,
                        'duration_ms': _elapsed_ms(started),
                    },
                )
                repository.reconcile_prior_receipts(
                    project_id,
                    notification.host,
                    notification.session_id,
                    current_receipt_id=receipt_id,
                )
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    event_count=session.event_count,
                    last_event_id=session.last_event_id,
                    boundary_id=boundary_id,
                    elapsed_ms=_elapsed_ms(started),
                )

            trajectory = self.adapter.normalize(
                notification, snapshot, trace_id
            )
            trajectory.metadata['capture_project_id'] = project_id
            prepared = prepare_for_capture(trajectory)
            trajectory = prepared.trajectory
            if not trajectory.events:
                warnings = ['no meaningful events']
                repository.commit_no_op(
                    receipt_id,
                    snapshot,
                    {
                        'boundary_id': boundary_id,
                        'warnings': warnings,
                        'duration_ms': _elapsed_ms(started),
                    },
                )
                repository.reconcile_prior_receipts(
                    project_id,
                    notification.host,
                    notification.session_id,
                    current_receipt_id=receipt_id,
                )
                current = repository.load_session(
                    notification.host, notification.session_id
                )
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    event_count=0 if current is None else current.event_count,
                    last_event_id=None if current is None else current.last_event_id,
                    boundary_id=boundary_id,
                    warnings=warnings,
                    elapsed_ms=_elapsed_ms(started),
                )
            if reconciliation and not _has_durable_assistant_boundary(trajectory):
                repository.commit_no_op(
                    receipt_id,
                    snapshot,
                    {
                        'boundary_id': boundary_id,
                        'warnings': ['no prior durable assistant boundary'],
                        'duration_ms': _elapsed_ms(started),
                    },
                )
                repository.reconcile_prior_receipts(
                    project_id,
                    notification.host,
                    notification.session_id,
                    current_receipt_id=receipt_id,
                )
                return CaptureResult(
                    status='no_op',
                    trace_id=trace_id,
                    boundary_id=boundary_id,
                    warnings=['no prior durable assistant boundary'],
                    elapsed_ms=_elapsed_ms(started),
                )
            repository.commit_capture(
                receipt_id,
                trajectory,
                snapshot,
                {
                    'project_id': project_id,
                    'boundary_id': boundary_id,
                    'warnings': [
                        f'{name}={count}'
                        for name, count in sorted(prepared.counters.items())
                    ],
                    'adapter_version': int(
                        getattr(self.adapter, 'version', 1)
                    ),
                    'duration_ms': _elapsed_ms(started),
                },
            )
            repository.reconcile_prior_receipts(
                project_id,
                notification.host,
                notification.session_id,
                current_receipt_id=receipt_id,
            )
            return CaptureResult(
                status='captured',
                trace_id=trace_id,
                event_count=len(trajectory.events),
                last_event_id=(
                    trajectory.events[-1].event_id if trajectory.events else None
                ),
                boundary_id=boundary_id,
                warnings=[
                    f'{name}={count}'
                    for name, count in sorted(prepared.counters.items())
                ],
                elapsed_ms=_elapsed_ms(started),
            )
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            elapsed = _elapsed_ms(started)
            if repository is not None and receipt_id is not None:
                try:
                    repository.mark_failed(receipt_id, str(exc), elapsed)
                except (OSError, ValueError, sqlite3.Error):
                    pass
            return CaptureResult(
                status='failed', warnings=[str(exc)], elapsed_ms=elapsed
            )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _has_durable_assistant_boundary(trajectory: object) -> bool:
    events = getattr(trajectory, 'events', [])
    return any(
        getattr(event, 'event_type', None) == 'llm.response' for event in events
    )
