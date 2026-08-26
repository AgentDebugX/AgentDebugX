"""CLI and silent hook dispatcher for automatic capture."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

from agentdebug.capture.config import load_capture_config
from agentdebug.capture.contracts import HookNotification
from agentdebug.capture.hosts.claude_code import ClaudeCodeCaptureAdapter
from agentdebug.capture.identity import project_id_for
from agentdebug.capture.repository import CaptureRepository
from agentdebug.capture.service import CaptureService
from agentdebug.integrations.capture_management import (
    _host_name,
    capture_integration_status,
    disable_capture_integration,
    enable_capture_integration,
)


def run(args: Any) -> int:
    project = Path(args.project).expanduser().resolve()
    if args.capture_command == 'dispatch':
        return _dispatch(args.platform, project)
    try:
        if args.capture_command == 'enable':
            payload = enable_capture_integration(args.platform, project)
        elif args.capture_command == 'disable':
            payload = disable_capture_integration(args.platform, project)
        elif args.capture_command == 'status':
            payload = _status(args.platform, project)
        elif args.capture_command == 'reconcile':
            payload = _reconcile(args.platform, project)
        else:
            return 1
    except (OSError, ValueError) as exc:
        print(f'capture {args.capture_command} failed: {exc}', file=sys.stderr)
        return 2
    if getattr(args, 'json', False):
        print(json.dumps(payload, default=str))
    else:
        print(
            f"capture {payload['status']}: {args.platform} -> {project}"
        )
    return 0


def _dispatch(platform: str, project: Path) -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        adapter = _adapter(platform)
        notification = adapter.parse_notification(payload)
        CaptureService(project, adapter).handle(notification)
    except sqlite3.OperationalError:
        # Hooks are passive sensors. Host execution must always continue.
        return 0
    return 0


def _status(platform: str, project: Path) -> Dict[str, Any]:
    payload = capture_integration_status(platform, project)
    config = load_capture_config(project)
    if config is None or not config.store_path.exists():
        payload.update(
            {
                'pending_receipts': 0,
                'failed_receipts': 0,
                'committed_receipts': 0,
                'sessions': [],
                'last_error': None,
            }
        )
        payload['status'] = 'enabled' if payload['enabled'] else 'disabled'
        return payload
    repository = CaptureRepository(config.store_path, initialize=False)
    try:
        status = repository.status(
            project_id_for(project), host=_host_name(platform)
        )
    except Exception:
        payload.update(
            {
                'pending_receipts': 0,
                'failed_receipts': 0,
                'committed_receipts': 0,
                'sessions': [],
                'last_error': None,
            }
        )
        payload['status'] = 'enabled' if payload['enabled'] else 'disabled'
        return payload
    payload.update(
        {
            'pending_receipts': status.pending_receipts,
            'failed_receipts': status.failed_receipts,
            'committed_receipts': status.committed_receipts,
            'sessions': [
                session.model_dump(mode='json')
                if hasattr(session, 'model_dump')
                else session.dict()
                for session in status.sessions
            ],
            'last_error': _last_error(
                repository, project_id_for(project), _host_name(platform)
            ),
        }
    )
    payload['status'] = 'enabled' if payload['enabled'] else 'disabled'
    return payload


def _reconcile(platform: str, project: Path) -> Dict[str, Any]:
    config = load_capture_config(project)
    host = _host_name(platform)
    platform_config = None if config is None else config.platforms.get(host)
    if platform_config is None or not platform_config.enabled:
        return {'status': 'disabled', 'replayed': 0, 'failed': 0}
    if not config.store_path.exists():
        return {'status': 'reconciled', 'replayed': 0, 'failed': 0}
    adapter = _adapter(platform)
    repository = CaptureRepository(config.store_path)
    receipts = repository.list_replayable(project_id_for(project), host=host)
    replayed = 0
    failed = 0
    for receipt in receipts:
        notification = HookNotification(
            host=receipt.host,
            event_name=receipt.native_event_name,
            session_id=receipt.session_id,
            transcript_path=receipt.transcript_path,
            cwd=receipt.cwd,
            observed_at=receipt.observed_at,
            native_event_id=receipt.native_event_id,
            task=receipt.task,
            session_end_reason=receipt.session_end_reason,
            native_payload=receipt.native_payload,
        )
        result = CaptureService(project, adapter).handle(notification)
        if result.status in {'captured', 'no_op'}:
            repository.mark_reconciled(receipt.receipt_id)
            replayed += 1
        else:
            failed += 1
    return {
        'status': 'reconciled' if failed == 0 else 'failed',
        'replayed': replayed,
        'failed': failed,
    }


def _adapter(platform: str) -> Any:
    if platform == 'claude':
        return ClaudeCodeCaptureAdapter()
    if platform == 'codex':
        from agentdebug.capture.hosts.codex import CodexCaptureAdapter

        return CodexCaptureAdapter()
    raise ValueError(f'unsupported capture platform: {platform}')


def _last_error(
    repository: CaptureRepository, project_id: str, host: str
) -> Any:
    replayable = repository.list_replayable(project_id, host=host)
    failed = [receipt for receipt in replayable if receipt.status == 'failed']
    return failed[-1].error if failed else None
