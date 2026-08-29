#!/usr/bin/env python3
"""Fail-open bridge from Claude Code hooks to the pinned AgentDebugX runtime."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


PACKAGE = 'agentdebugx==0.4.0'
CLI_PLATFORM = 'claude'
CONFIG_PLATFORM = 'claude_code'


def main() -> int:
    raw_payload = sys.stdin.read()
    try:
        payload = json.loads(raw_payload)
        project = _enabled_project(Path(payload['cwd']))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0
    command = _runtime_command()
    if project is None or command is None:
        return 0
    try:
        subprocess.run(
            command
            + [
                'integrations',
                'capture',
                'dispatch',
                '--platform',
                CLI_PLATFORM,
            ],
            input=raw_payload,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass
    return 0


def _runtime_command():
    override = os.environ.get('AGENTDEBUGX_PLUGIN_CLI')
    if override:
        return shlex.split(override) or None
    uvx = shutil.which('uvx')
    if uvx is None:
        return None
    return [uvx, '--quiet', '--from', PACKAGE, 'agentdebug']


def _enabled_project(cwd: Path):
    root = cwd.expanduser().resolve()
    for candidate in (root, *root.parents):
        try:
            config = json.loads(
                (candidate / '.agentdebug' / 'capture.json').read_text(encoding='utf-8')
            )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        platform = config.get('platforms', {}).get(CONFIG_PLATFORM, {})
        if platform.get('enabled') is True:
            return candidate
    return None


if __name__ == '__main__':
    raise SystemExit(main())
