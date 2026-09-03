"""Ownership-aware host skill installation and readiness checks."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Optional

from agentdebug import __version__

from .debug_skill import CONTRACT_VERSION, build_debug_skill_bundle

MARKER = '.agentdebug-managed.json'
DEFAULT_ROOTS = {
    'claude': '~/.claude/skills',
    'codex': '~/.agents/skills',
    'hermes': '~/.hermes/skills',
    'openclaw': '~/.openclaw/skills',
}


def install_skill(
    platform: str, *, target: Optional[str] = None, name: str = 'agentdebug',
    force: bool = False,
) -> dict[str, Any]:
    bundle = build_debug_skill_bundle(platform=platform, name=name)  # type: ignore[arg-type]
    root = Path(target or DEFAULT_ROOTS[platform]).expanduser() / name
    marker_path = root / MARKER
    marker = _read_marker(marker_path)
    marker_valid = bool(
        marker
        and marker.get('managed_by') == 'AgentDebugX'
        and marker.get('platform') == platform
        and isinstance(marker.get('owned_files'), list)
    )
    if root.exists() and not marker_valid and any(root.iterdir()) and not force:
        raise ValueError(f'refusing to overwrite unmanaged skill directory: {root}')
    root.mkdir(parents=True, exist_ok=True)
    owned_before = set(marker.get('owned_files', [])) if marker_valid and marker else set()
    for rel in owned_before - set(bundle.files):
        path = root / rel
        if path.is_file():
            path.unlink()
    for rel, content in bundle.files.items():
        path = root / rel
        if path.exists() and rel not in owned_before and not force:
            raise ValueError(f'refusing to overwrite unmanaged skill file: {path}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    managed = {
        'schema_version': 1, 'managed_by': 'AgentDebugX', 'platform': platform,
        'package_version': __version__, 'contract_version': CONTRACT_VERSION,
        'owned_files': sorted(bundle.files),
    }
    _atomic_json(marker_path, managed)
    return {'status': 'installed', 'platform': platform, 'path': str(root), 'marker': managed, 'reload_required': True}


def integration_status(
    platform: str, *, target: Optional[str] = None, name: str = 'agentdebug',
    run_root: str = '.agentdebug',
) -> dict[str, Any]:
    root = Path(target or DEFAULT_ROOTS[platform]).expanduser() / name
    marker = _read_marker(root / MARKER)
    try:
        from agentdebug.cli.main import main as cli_main

        cli_callable = callable(cli_main)
    except ImportError:
        cli_callable = False
    checks = {
        'skill_path': root.is_dir(),
        'managed': bool(marker and marker.get('managed_by') == 'AgentDebugX'),
        'contract_current': bool(marker and marker.get('contract_version') == CONTRACT_VERSION),
        'package_importable': importlib.util.find_spec('agentdebug') is not None,
        'cli_callable': cli_callable,
        'ui_dependencies': importlib.util.find_spec('fastapi') is not None and importlib.util.find_spec('uvicorn') is not None,
        'run_root_writable': _writable(Path(run_root).expanduser()),
    }
    return {
        'schema_version': 1, 'platform': platform, 'path': str(root),
        'ready': all(value for key, value in checks.items() if key != 'ui_dependencies'),
        'checks': checks, 'marker': marker,
    }


def _read_marker(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def _writable(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)
