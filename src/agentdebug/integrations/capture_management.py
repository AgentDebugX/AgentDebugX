"""Project consent management for plugin-provided automatic capture."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from agentdebug.capture.config import (
    disable_capture,
    enable_capture,
    load_capture_config,
)
from agentdebug.capture.hosts.registry import get_capture_host


def enable_capture_consent(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    host = get_capture_host(platform)
    config = enable_capture(root, host.host_name, list(host.event_boundaries))
    return {
        'status': 'enabled',
        'platform': platform,
        'project_root': str(root),
        'store_path': str(config.store_path),
        'hook_source': 'plugin',
        'capture_events': list(host.event_boundaries),
    }


def disable_capture_consent(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    host = get_capture_host(platform)
    config = load_capture_config(root)
    if config is not None:
        disable_capture(root, host.host_name)
    return {
        'status': 'disabled',
        'platform': platform,
        'project_root': str(root),
        'store_path': None if config is None else str(config.store_path),
        'hook_source': 'plugin',
    }


def capture_consent_status(platform: str, project: Path) -> Dict[str, Any]:
    root = project.expanduser().resolve()
    host = get_capture_host(platform)
    config = load_capture_config(root)
    platform_config = (
        None if config is None else config.platforms.get(host.host_name)
    )
    return {
        'schema_version': 1,
        'platform': platform,
        'project_root': str(root),
        'enabled': bool(platform_config and platform_config.enabled),
        'store_path': str(config.store_path) if config else None,
        'hook_source': 'plugin',
        'capture_events': (
            [] if platform_config is None else platform_config.capture_events
        ),
    }
