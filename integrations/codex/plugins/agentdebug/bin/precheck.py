"""Standard-library consent probe for the AgentDebugX capture hook.

Reads one host hook payload on stdin and exits 0 only when the project that
owns the payload's ``cwd`` has consented to capture for this platform. It
imports nothing from ``agentdebug`` so that a session in an unconsented
project never pays for the CLI's analyzer and HTTP imports.

Exit codes: 0 dispatch, 1 skip. Any unexpected condition returns 0 so the
authoritative check stays in the CLI.
"""

import json
import os
import sys

HOSTS = {'claude': 'claude_code', 'codex': 'codex'}


def main() -> int:
    platform = sys.argv[1] if len(sys.argv) > 1 else ''
    host = HOSTS.get(platform)
    if host is None:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or '{}')
        cwd = payload.get('cwd')
    except Exception:
        return 0
    if not isinstance(cwd, str) or not cwd:
        return 0

    directory = os.path.abspath(os.path.expanduser(cwd))
    while True:
        config = os.path.join(directory, '.agentdebug', 'capture.json')
        if os.path.isfile(config):
            try:
                with open(config, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except Exception:
                return 0
            platforms = data.get('platforms')
            if isinstance(platforms, dict):
                settings = platforms.get(host)
                if isinstance(settings, dict):
                    return 0 if settings.get('enabled', True) else 1
                return 1
            # Schema v1 stored one platform per file.
            if data.get('platform') == host:
                return 0 if data.get('enabled', True) else 1
            return 1
        parent = os.path.dirname(directory)
        if parent == directory:
            return 1
        directory = parent


if __name__ == '__main__':
    sys.exit(main())
