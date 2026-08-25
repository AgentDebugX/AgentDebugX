"""Managed inspection UI commands."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from agentdebug.inspect.ui.manager import ensure_ui, ui_status
from agentdebug.workbench.registry import RunRegistry


def run(args: Any) -> int:
    registry = RunRegistry(args.run_root)
    try:
        handle = ensure_ui(args.run_id, run_registry=registry, open_browser=args.open_browser) if args.ui_command == 'ensure' else ui_status(registry)
    except (KeyError, ValueError) as exc:
        print(f'ui failed: {exc}', file=__import__('sys').stderr)
        return 2
    payload = asdict(handle)
    print(json.dumps(payload) if args.json else (handle.run_url or handle.base_url or handle.status))
    return 0 if handle.status == 'ready' else 5


__all__ = ['run']
