"""Session-scoped context for diagnosing the current captured trajectory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

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


def validate_current_capture_context(
    context: CurrentCaptureContext, *, cwd: Optional[Path] = None
) -> CurrentCaptureContext:
    working_directory = (cwd or Path.cwd()).expanduser().resolve()
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


def read_current_capture_context(value: str) -> CurrentCaptureContext:
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


__all__ = [
    'CURRENT_CAPTURE_CONTEXT_ENV',
    'CurrentCaptureContext',
    'read_current_capture_context',
    'validate_current_capture_context',
    'write_current_capture_context',
]
