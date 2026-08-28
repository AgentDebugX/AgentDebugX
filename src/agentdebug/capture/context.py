"""Session-scoped context for diagnosing the current captured trajectory."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Mapping, Optional

from pydantic import BaseModel

from agentdebug.capture.config import load_capture_config
from agentdebug.capture.contracts import HookNotification
from agentdebug.capture.identity import trace_id_for


CURRENT_CAPTURE_CONTEXT_ENV = 'AGENTDEBUG_CAPTURE_CONTEXT'


class CurrentCaptureContext(BaseModel):
    schema_version: int = 1
    host: str
    session_id: str
    project_root: Path
    store_path: Path
    trace_id: str


def write_current_capture_context(
    project_root: Path, notification: HookNotification
) -> Path:
    root = project_root.expanduser().resolve()
    config = load_capture_config(root)
    if config is None:
        raise ValueError(f'capture is not configured for {root}')
    trace_id = trace_id_for(notification.host, notification.session_id)
    context = CurrentCaptureContext(
        host=notification.host,
        session_id=notification.session_id,
        project_root=root,
        store_path=config.store_path.expanduser().resolve(),
        trace_id=trace_id,
    )
    path = root / '.agentdebug' / 'capture-context' / f'{trace_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        context.model_dump(mode='json')
        if hasattr(context, 'model_dump')
        else json.loads(context.json())
    )
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def expose_current_capture_context(
    notification: HookNotification,
    context_path: Path,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> None:
    if (
        notification.host != 'claude_code'
        or notification.event_name != 'SessionStart'
    ):
        return
    values = os.environ if environ is None else environ
    env_file = values.get('CLAUDE_ENV_FILE')
    if not env_file:
        return
    with Path(env_file).expanduser().open('a', encoding='utf-8') as handle:
        handle.write(
            f'export {CURRENT_CAPTURE_CONTEXT_ENV}='
            f'{shlex.quote(str(context_path.expanduser().resolve()))}\n'
        )


def load_current_capture_context(
    *, environ: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None
) -> CurrentCaptureContext:
    values = os.environ if environ is None else environ
    context_value = values.get(CURRENT_CAPTURE_CONTEXT_ENV)
    working_directory = (cwd or Path.cwd()).expanduser().resolve()
    context = (
        _read_context_file(context_value)
        if context_value
        else _context_from_codex_environment(values, working_directory)
    )
    root = context.project_root.expanduser().resolve()
    try:
        working_directory.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f'current capture context belongs to a different project: {root}'
        ) from exc
    expected_trace_id = trace_id_for(context.host, context.session_id)
    if context.trace_id != expected_trace_id:
        raise ValueError('current capture context has an invalid trace ID')
    config = load_capture_config(root)
    platform = None if config is None else config.platforms.get(context.host)
    if platform is None or not platform.enabled:
        raise ValueError(
            f'automatic capture is not enabled for {context.host} in {root}'
        )
    configured_store = config.store_path.expanduser().resolve()
    if context.store_path.expanduser().resolve() != configured_store:
        raise ValueError(
            'current capture context does not match the configured store'
        )
    context.project_root = root
    context.store_path = configured_store
    return context


def _read_context_file(value: str) -> CurrentCaptureContext:
    path = Path(value).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError(f'current capture context does not exist: {path}') from exc
    validator = getattr(CurrentCaptureContext, 'model_validate', None)
    return (
        validator(payload)
        if callable(validator)
        else CurrentCaptureContext.parse_obj(payload)
    )


def _context_from_codex_environment(
    environ: Mapping[str, str], cwd: Path
) -> CurrentCaptureContext:
    session_id = environ.get('CODEX_THREAD_ID') or environ.get('CODEX_SESSION_ID')
    if session_id:
        for root in (cwd, *cwd.parents):
            config = load_capture_config(root)
            platform = None if config is None else config.platforms.get('codex')
            if config is None or platform is None or not platform.enabled:
                continue
            return CurrentCaptureContext(
                host='codex',
                session_id=session_id,
                project_root=root,
                store_path=config.store_path.expanduser().resolve(),
                trace_id=trace_id_for('codex', session_id),
            )
    raise ValueError(
        'no current captured session context; provide a trajectory or invoke '
        'AgentDebugX from a supported host plugin'
    )


__all__ = [
    'CURRENT_CAPTURE_CONTEXT_ENV',
    'CurrentCaptureContext',
    'expose_current_capture_context',
    'load_current_capture_context',
    'write_current_capture_context',
]
