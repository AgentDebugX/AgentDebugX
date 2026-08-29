"""Claude Code hook parsing and stable transcript normalization."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agentdebug.capture.context import (
    CURRENT_CAPTURE_CONTEXT_ENV,
    CurrentCaptureContext,
    read_current_capture_context,
    write_current_capture_context,
)
from agentdebug.capture.contracts import HookNotification, TranscriptSnapshot
from agentdebug.capture.identity import event_id_for
from agentdebug.ingest import convert_payload
from agentdebug.schema import AgentTrajectory

CLAUDE_EVENTS = {
    'SessionStart': 'session_start_reconciled',
    'UserPromptSubmit': 'pre_prompt_reconciled',
    'Stop': 'turn_completed',
    'TaskCompleted': 'task_completion_requested',
    'SessionEnd': 'session_reconciled',
}


class ClaudeCodeCaptureHost:
    cli_name = 'claude'
    host_name = 'claude_code'
    event_boundaries = CLAUDE_EVENTS

    def create_adapter(self) -> ClaudeCodeCaptureAdapter:
        return ClaudeCodeCaptureAdapter()

    def settings_path(self, project_root: Path) -> Path:
        return project_root / '.claude' / 'settings.json'

    def prepare_session_context(
        self,
        project_root: Path,
        notification: HookNotification,
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        if notification.event_name != 'SessionStart':
            return
        values = os.environ if environ is None else environ
        env_file = values.get('CLAUDE_ENV_FILE')
        if not env_file:
            return

        # Persist the exact session-to-trace mapping before transcript capture.
        context_path = write_current_capture_context(project_root, notification)

        # Claude sources this file before later Bash commands in the session.
        assignment = (
            f'export {CURRENT_CAPTURE_CONTEXT_ENV}='
            f'{shlex.quote(str(context_path.expanduser().resolve()))}'
        )
        env_path = Path(env_file).expanduser()
        try:
            existing = env_path.read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            existing = []

        # Resumes may reuse the environment file, so export the path only once.
        if assignment in existing:
            return

        with env_path.open('a', encoding='utf-8') as handle:
            handle.write(f'{assignment}\n')

    def resolve_current_context(
        self,
        environ: Mapping[str, str],
        cwd: Path,
    ) -> Optional[CurrentCaptureContext]:
        context_path = environ.get(CURRENT_CAPTURE_CONTEXT_ENV)
        if not context_path:
            return None
        context = read_current_capture_context(context_path)
        if context.host != self.host_name:
            raise ValueError(
                'current capture context does not belong to Claude Code'
            )
        return context


class ClaudeCodeCaptureAdapter:
    host = 'claude_code'
    version = 1
    event_boundaries = CLAUDE_EVENTS

    def __init__(self, *, transcript_root: Optional[Path] = None) -> None:
        self.transcript_root = (
            transcript_root or Path.home() / '.claude' / 'projects'
        ).expanduser().resolve()

    def parse_notification(self, payload: Dict[str, Any]) -> HookNotification:
        event_name = _required_text(payload, 'hook_event_name')
        if event_name not in CLAUDE_EVENTS:
            raise ValueError(f'unsupported Claude Code hook event: {event_name}')
        session_id = _required_text(payload, 'session_id')
        transcript_text = _required_text(payload, 'transcript_path')
        cwd_text = _required_text(payload, 'cwd')
        transcript_path = Path(transcript_text).expanduser()
        cwd = Path(cwd_text).expanduser()
        if not transcript_path.is_absolute() or not cwd.is_absolute():
            raise ValueError('Claude Code transcript_path and cwd must be absolute')
        native_event_id = None
        task = None
        if event_name == 'TaskCompleted':
            native_event_id = _optional_text(payload.get('task_id'))
            task = {
                key: payload[key]
                for key in ('task_id', 'task_subject', 'task_description', 'teammate_name', 'team_name')
                if payload.get(key) is not None
            }
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
            task=task,
            session_end_reason=_optional_text(payload.get('reason'))
            if event_name == 'SessionEnd'
            else None,
            native_payload=native_payload,
        )

    def validate_transcript_path(self, notification: HookNotification) -> Path:
        path = notification.transcript_path.expanduser().resolve(strict=True)
        try:
            path.relative_to(self.transcript_root)
        except ValueError as exc:
            raise ValueError(
                f'transcript is outside the Claude Code transcript root: {path}'
            ) from exc
        if not path.is_file():
            raise ValueError(f'Claude Code transcript is not a file: {path}')
        return path

    def normalize(
        self,
        notification: HookNotification,
        snapshot: TranscriptSnapshot,
        trace_id: str,
    ) -> AgentTrajectory:
        trajectory = convert_payload(
            snapshot.records,
            format='claude_code',
            trace_id=trace_id,
            framework='claude_code',
        )
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
                'capture_boundary_kind': CLAUDE_EVENTS[notification.event_name],
                'capture_ignored_tail_bytes': snapshot.ignored_tail_bytes,
                'capture_adapter_version': self.version,
            }
        )
        if notification.task is not None:
            trajectory.metadata['capture_task_signal'] = {
                **notification.task,
                'outcome': 'completion_requested',
            }
        if notification.session_end_reason is not None:
            trajectory.metadata['capture_session_end_reason'] = (
                notification.session_end_reason
            )
        return trajectory


def _stabilize_event_ids(
    trajectory: AgentTrajectory, session_id: str
) -> None:
    old_to_new: Dict[str, str] = {}
    occurrences: Dict[str, int] = defaultdict(int)
    for event in trajectory.events:
        old_id = event.event_id
        metadata = event.metadata
        native_uuid = _optional_text(metadata.get('claude_code_uuid'))
        message_id = _optional_text(metadata.get('claude_code_message_id'))
        block_index = str(metadata.get('claude_code_block_index', ''))
        block_type = _optional_text(metadata.get('claude_code_block_type')) or ''
        tool_id = _optional_text(metadata.get('claude_code_tool_use_id')) or ''
        if native_uuid or message_id or tool_id:
            parts = (
                native_uuid or '',
                message_id or '',
                block_index,
                str(event.event_type),
                block_type,
                tool_id,
            )
        else:
            canonical = json.dumps(
                {
                    'agent_name': event.agent_name,
                    'event_type': event.event_type,
                    'module': event.module,
                    'input': event.input,
                    'output': event.output,
                    'error': event.error,
                    'metadata': metadata,
                },
                sort_keys=True,
                separators=(',', ':'),
                default=str,
            )
            digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            occurrence = occurrences[digest]
            occurrences[digest] += 1
            parts = ('fallback', digest, str(occurrence))
        event.event_id = event_id_for('claude_code', session_id, parts)
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
        raise ValueError(f'Claude Code hook payload requires {key}')
    return value


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
