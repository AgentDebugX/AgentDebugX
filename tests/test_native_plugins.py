import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PLUGINS = [
    (
        ROOT / 'integrations' / 'claude-code' / 'plugins' / 'agentdebug',
        Path('hooks/hooks.json'),
        'claude',
        {'SessionStart', 'UserPromptSubmit', 'Stop', 'TaskCompleted', 'SessionEnd'},
    ),
    (
        ROOT / 'integrations' / 'codex' / 'plugins' / 'agentdebug',
        Path('hooks/hooks.json'),
        'codex',
        {'SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'},
    ),
]


@pytest.mark.parametrize('plugin_root,hooks_path,platform,events', PLUGINS)
def test_native_plugin_hooks_use_portable_cli_command(
    plugin_root: Path,
    hooks_path: Path,
    platform: str,
    events: set,
) -> None:
    payload = json.loads((plugin_root / hooks_path).read_text(encoding='utf-8'))
    hooks = payload['hooks']

    assert set(hooks) == events
    commands = {
        hook['command']
        for groups in hooks.values()
        for group in groups
        for hook in group['hooks']
    }
    assert commands == {
        f'agentdebug integrations capture dispatch --platform {platform}'
    }


def test_repository_marketplaces_resolve_plugin_roots() -> None:
    claude_marketplace = json.loads(
        (ROOT / '.claude-plugin' / 'marketplace.json').read_text(encoding='utf-8')
    )
    codex_marketplace = json.loads(
        (ROOT / '.agents' / 'plugins' / 'marketplace.json').read_text(encoding='utf-8')
    )

    claude_source = claude_marketplace['plugins'][0]['source']
    codex_source = codex_marketplace['plugins'][0]['source']['path']
    assert (ROOT / claude_source).is_dir()
    assert (ROOT / codex_source).is_dir()
