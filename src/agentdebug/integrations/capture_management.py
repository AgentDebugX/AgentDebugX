"""Ownership-aware project hook activation for automatic capture."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict

from agentdebug import __version__
from agentdebug.capture.config import (
    CaptureConfig,
    disable_capture,
    load_capture_config,
    write_capture_config,
)
from agentdebug.integrations.capture_templates import (
    CAPTURE_EVENTS,
    capture_hook_groups,
    is_agentdebug_capture_group,
)


def enable_capture_integration(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    host = _host_name(platform)
    settings_path = _settings_path(platform, root)
    managed_root = root / '.agentdebug' / 'capture-hooks' / platform
    launcher_path = managed_root / 'dispatch.sh'
    marker_path = managed_root / 'ownership.json'
    managed_root.mkdir(parents=True, exist_ok=True)
    launcher_command = str(launcher_path)
    launcher = (
        '#!/bin/sh\n'
        + 'exec '
        + ' '.join(
            shlex.quote(value)
            for value in (
                sys.executable,
                '-m',
                'agentdebug.cli',
                'integrations',
                'capture',
                'dispatch',
                '--platform',
                platform,
                '--project',
                str(root),
            )
        )
        + '\n'
    )
    _atomic_text(launcher_path, launcher, mode=0o700)
    marker = {
        'schema_version': 1,
        'managed_by': 'AgentDebugX',
        'package_version': __version__,
        'platform': platform,
        'launcher': launcher_command,
        'settings_path': str(settings_path),
        'owned_events': CAPTURE_EVENTS[platform],
    }
    _atomic_json(marker_path, marker)

    settings = _read_settings(settings_path)
    hooks = settings.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError(f'host hooks setting must be an object: {settings_path}')
    generated = capture_hook_groups(platform, launcher_command)
    for event, groups in generated.items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f'host hook event {event} must be a list')
        hooks[event] = [
            group for group in existing if not is_agentdebug_capture_group(group)
        ] + groups
    _atomic_json(settings_path, settings)

    store_path = root / '.agentdebug' / 'agentdebug.sqlite'
    write_capture_config(
        CaptureConfig(
            enabled=True,
            platform=host,
            project_root=root,
            store_path=store_path,
            installed_hooks=CAPTURE_EVENTS[platform],
        )
    )
    return {
        'status': 'enabled',
        'platform': platform,
        'project_root': str(root),
        'store_path': str(store_path),
        'settings_path': str(settings_path),
        'launcher_path': str(launcher_path),
        'ownership_path': str(marker_path),
        'installed_hooks': CAPTURE_EVENTS[platform],
    }


def disable_capture_integration(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    config = load_capture_config(root)
    if config is not None:
        disable_capture(root)
    settings_path = _settings_path(platform, root)
    settings = _read_settings(settings_path)
    hooks = settings.get('hooks')
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            remaining = [
                group for group in groups if not is_agentdebug_capture_group(group)
            ]
            if remaining:
                hooks[event] = remaining
            else:
                hooks.pop(event)
        if not hooks:
            settings.pop('hooks', None)
        _atomic_json(settings_path, settings)
    return {
        'status': 'disabled',
        'platform': platform,
        'project_root': str(root),
        'store_path': None if config is None else str(config.store_path),
        'settings_path': str(settings_path),
    }


def capture_integration_status(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    config = load_capture_config(root)
    settings_path = _settings_path(platform, root)
    settings = _read_settings(settings_path)
    hooks = settings.get('hooks', {})
    detected = []
    duplicates = []
    if isinstance(hooks, dict):
        for event in CAPTURE_EVENTS[platform]:
            groups = hooks.get(event, [])
            count = sum(
                is_agentdebug_capture_group(group)
                for group in groups
                if isinstance(groups, list)
            )
            if count:
                detected.append(event)
            if count > 1:
                duplicates.append(event)
    managed_root = root / '.agentdebug' / 'capture-hooks' / platform
    launcher_path = managed_root / 'dispatch.sh'
    return {
        'schema_version': 1,
        'platform': platform,
        'project_root': str(root),
        'enabled': bool(config and config.enabled),
        'store_path': str(config.store_path) if config else None,
        'settings_path': str(settings_path),
        'launcher_path': str(launcher_path),
        'launcher_ready': launcher_path.is_file() and os.access(launcher_path, os.X_OK),
        'installed_hooks': [] if config is None else config.installed_hooks,
        'detected_hooks': detected,
        'duplicate_hooks': duplicates,
    }


def _host_name(platform: str) -> str:
    if platform == 'claude':
        return 'claude_code'
    if platform == 'codex':
        return 'codex'
    raise ValueError(f'unsupported capture platform: {platform}')


def _settings_path(platform: str, root: Path) -> Path:
    if platform == 'claude':
        return root / '.claude' / 'settings.json'
    if platform == 'codex':
        return root / '.codex' / 'hooks.json'
    raise ValueError(f'unsupported capture platform: {platform}')


def _read_settings(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise ValueError(f'host settings must be a JSON object: {path}')
    return payload


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, indent=2) + '\n')


def _atomic_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(content, encoding='utf-8')
    os.chmod(temp, mode)
    os.replace(temp, path)
