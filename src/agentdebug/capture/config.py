"""Project-scoped automatic capture configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel

PlatformName = Literal['claude_code', 'codex']


class CaptureConfig(BaseModel):
    schema_version: int = 1
    enabled: bool = True
    platform: PlatformName
    project_root: Path
    store_path: Path
    installed_hooks: List[str]


def capture_config_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / '.agentdebug' / 'capture.json'


def load_capture_config(project_root: Path) -> Optional[CaptureConfig]:
    path = capture_config_path(project_root)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    validator = getattr(CaptureConfig, 'model_validate', None)
    return validator(payload) if callable(validator) else CaptureConfig.parse_obj(payload)


def write_capture_config(config: CaptureConfig) -> Path:
    project_root = config.project_root.expanduser().resolve()
    path = capture_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': config.schema_version,
        'enabled': config.enabled,
        'platform': config.platform,
        'project_root': str(project_root),
        'store_path': str(config.store_path.expanduser().resolve()),
        'installed_hooks': config.installed_hooks,
    }
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def disable_capture(project_root: Path) -> CaptureConfig:
    config = load_capture_config(project_root)
    if config is None:
        raise ValueError(f'capture is not configured for {project_root}')
    config.enabled = False
    write_capture_config(config)
    return config
