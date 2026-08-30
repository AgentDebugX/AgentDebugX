"""Synchronize native plugin skills from the canonical AgentDebugX bundle."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentdebug.integrations.debug_skill import build_debug_skill_bundle


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'claude': ROOT / 'integrations/claude-code/plugins/agentdebug/skills/agentdebug',
    'codex': ROOT / 'integrations/codex/plugins/agentdebug/skills/agentdebug',
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    stale: list[str] = []
    for platform, target in TARGETS.items():
        bundle = build_debug_skill_bundle(platform=platform)
        for relative, content in bundle.files.items():
            path = target / relative
            if args.check:
                if not path.is_file() or path.read_text(encoding='utf-8') != content:
                    stale.append(str(path.relative_to(ROOT)))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    if stale:
        print('stale generated plugin skills:')
        print('\n'.join(f'- {path}' for path in stale))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
