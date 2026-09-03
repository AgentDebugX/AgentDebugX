"""Codex hook and rollout adapter."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agentdebug.capture.config import load_capture_config
from agentdebug.capture.context import CurrentCaptureContext
from agentdebug.capture.contracts import HookNotification, TranscriptSnapshot
from agentdebug.capture.identity import event_id_for
from agentdebug.capture.repository import CaptureRepository
from agentdebug.ingest import convert_payload
from agentdebug.schema import AgentTrajectory

CODEX_EVENTS = {
    'SessionStart': 'session_start_reconciled',
    'UserPromptSubmit': 'pre_prompt_reconciled',
    'Stop': 'turn_completed',
    'SessionEnd': 'session_reconciled',
}


class CodexCaptureHost:
    cli_name = 'codex'
    host_name = 'codex'
    event_boundaries = CODEX_EVENTS

    def create_adapter(self) -> CodexCaptureAdapter:
        return CodexCaptureAdapter()

    def prepare_session_context(
        self,
        project_root: Path,
        notification: HookNotification,
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None

    def resolve_current_context(
        self,
        environ: Mapping[str, str],
        cwd: Path,
    ) -> Optional[CurrentCaptureContext]:
        session_id = environ.get('CODEX_THREAD_ID') or environ.get(
            'CODEX_SESSION_ID'
        )
        if not session_id:
            return None
        for root in (cwd, *cwd.parents):
            config = load_capture_config(root)
            platform = None if config is None else config.platforms.get(self.host_name)
            if config is None or platform is None or not platform.enabled:
                continue
            session = CaptureRepository(config.store_path).load_session(
                self.host_name, session_id
            )
            return CurrentCaptureContext(
                host=self.host_name,
                session_id=session_id,
                project_root=root,
                store_path=config.store_path.expanduser().resolve(),
                trace_id=None if session is None else session.trace_id,
            )
        raise ValueError(
            f'automatic capture is not enabled for Codex in {cwd}'
        )


class CodexCaptureAdapter:
    host = 'codex'
    version = 1
    event_boundaries = CODEX_EVENTS

    def __init__(self, *, transcript_root: Optional[Path] = None) -> None:
        self.transcript_root = (
            transcript_root or Path.home() / '.codex' / 'sessions'
        ).expanduser().resolve()

    def parse_notification(self, payload: Dict[str, Any]) -> HookNotification:
        event_name = _required_text(payload, 'hook_event_name')
        if event_name not in CODEX_EVENTS:
            raise ValueError(f'unsupported Codex hook event: {event_name}')
        session_id = _required_text(payload, 'session_id')
        transcript_path = Path(_required_text(payload, 'transcript_path')).expanduser()
        cwd = Path(_required_text(payload, 'cwd')).expanduser()
        if not transcript_path.is_absolute() or not cwd.is_absolute():
            raise ValueError('Codex transcript_path and cwd must be absolute')
        native_event_id = (
            _optional_text(payload.get('turn_id'))
            if event_name in {'UserPromptSubmit', 'Stop'}
            else None
        )
        native_payload = {
            key: payload[key]
            for key in ('source', 'model', 'permission_mode', 'stop_hook_active')
            if payload.get(key) is not None
        }
        return HookNotification(
            host=self.host,
            event_name=event_name,
            session_id=session_id,
            transcript_path=transcript_path,
            cwd=cwd,
            observed_at=datetime.now(timezone.utc),
            native_event_id=native_event_id,
            session_end_reason=(
                _optional_text(payload.get('reason'))
                if event_name == 'SessionEnd'
                else None
            ),
            native_payload=native_payload,
        )

    def validate_transcript_path(self, notification: HookNotification) -> Path:
        path = notification.transcript_path.expanduser().resolve(strict=True)
        try:
            path.relative_to(self.transcript_root)
        except ValueError as exc:
            raise ValueError(
                f'transcript is outside the Codex session root: {path}'
            ) from exc
        if not path.is_file():
            raise ValueError(f'Codex transcript is not a file: {path}')
        return path

    def normalize(
        self,
        notification: HookNotification,
        snapshot: TranscriptSnapshot,
        trace_id: str,
    ) -> AgentTrajectory:
        trajectory = convert_payload(
            snapshot.records,
            format='codex',
            trace_id=trace_id,
            framework='codex',
        )
        native_session = trajectory.metadata.get('codex_session_id')
        if native_session and native_session != notification.session_id:
            raise ValueError('Codex hook session does not match rollout session')
        if notification.event_name == 'UserPromptSubmit':
            while trajectory.events:
                event = trajectory.events[-1]
                if event.agent_name != 'user' or event.module != 'conversation':
                    break
                trajectory.events.pop()
        _stabilize_event_ids(trajectory, notification.session_id)
        trajectory.metadata.update(
            {
                'capture_host': self.host,
                'capture_host_session_id': notification.session_id,
                'capture_snapshot_sha256': snapshot.content_sha256,
                'capture_boundary_kind': CODEX_EVENTS[notification.event_name],
                'capture_ignored_tail_bytes': snapshot.ignored_tail_bytes,
                'capture_adapter_version': self.version,
            }
        )
        if notification.session_end_reason is not None:
            trajectory.metadata['capture_session_end_reason'] = (
                notification.session_end_reason
            )
        return trajectory


def _stabilize_event_ids(trajectory: AgentTrajectory, session_id: str) -> None:
    old_to_new: Dict[str, str] = {}
    occurrences: Dict[str, int] = defaultdict(int)
    for event in trajectory.events:
        old_id = event.event_id
        metadata = event.metadata
        item_id = _optional_text(metadata.get('codex_id')) or ''
        call_id = _optional_text(metadata.get('codex_call_id')) or ''
        turn_id = _optional_text(metadata.get('codex_turn_id')) or ''
        line = str(metadata.get('codex_line', ''))
        if item_id or call_id or turn_id:
            parts = (
                item_id,
                call_id,
                turn_id,
                str(event.event_type),
                line,
            )
        else:
            canonical = json.dumps(
                {
                    'line': line,
                    'event_type': event.event_type,
                    'agent_name': event.agent_name,
                    'input': event.input,
                    'output': event.output,
                    'error': event.error,
                },
                sort_keys=True,
                separators=(',', ':'),
                default=str,
            )
            digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            occurrence = occurrences[digest]
            occurrences[digest] += 1
            parts = ('fallback', digest, str(occurrence))
        event.event_id = event_id_for('codex', session_id, parts)
        event.trace_id = trajectory.trace_id
        old_to_new[old_id] = event.event_id
    for event in trajectory.events:
        if event.parent_event_id is not None:
            event.parent_event_id = old_to_new.get(
                event.parent_event_id, event.parent_event_id
            )


def _required_text(payload: Dict[str, Any], key: str) -> str:
    value = _optional_text(payload.get(key))
    if value is None:
        raise ValueError(f'Codex hook payload requires {key}')
    return value


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
