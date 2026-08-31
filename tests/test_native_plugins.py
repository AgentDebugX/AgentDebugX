import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PLUGINS = [
    (
        ROOT / 'integrations' / 'claude-code' / 'plugins' / 'agentdebug',
        'claude',
        'CLAUDE_PLUGIN_ROOT',
        {'SessionStart', 'UserPromptSubmit', 'Stop', 'TaskCompleted', 'SessionEnd'},
    ),
    (
        ROOT / 'integrations' / 'codex' / 'plugins' / 'agentdebug',
        'codex',
        'PLUGIN_ROOT',
        {'SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'},
    ),
]
IDS = ['claude', 'codex']


@pytest.mark.parametrize('plugin_root,platform,root_var,events', PLUGINS, ids=IDS)
def test_hooks_launch_the_bundled_portable_launcher(
    plugin_root: Path, platform: str, root_var: str, events: set,
) -> None:
    """Hooks must not depend on `agentdebug` resolving through PATH."""
    payload = json.loads(
        (plugin_root / 'hooks' / 'hooks.json').read_text(encoding='utf-8')
    )
    assert set(payload['hooks']) == events
    hooks = [
        hook
        for groups in payload['hooks'].values()
        for group in groups
        for hook in group['hooks']
    ]
    assert hooks
    for hook in hooks:
        assert hook['command'] == (
            f'sh "${{{root_var}}}/bin/agentdebug-hook" --platform {platform}'
        )
        assert hook['commandWindows'] == (
            f'cmd.exe /d /s /c call "%{root_var}%\\bin\\agentdebug-hook.cmd" '
            f'{platform}'
        )


@pytest.mark.parametrize('plugin_root,platform,root_var,events', PLUGINS, ids=IDS)
def test_launcher_assets_are_present_and_executable(
    plugin_root: Path, platform: str, root_var: str, events: set,
) -> None:
    launcher = plugin_root / 'bin' / 'agentdebug-hook'
    for name in ('agentdebug-hook', 'agentdebug-hook.cmd', 'agentdebug-hook.ps1',
                 'precheck.py'):
        assert (plugin_root / 'bin' / name).is_file()
    assert os.access(launcher, os.X_OK)


@pytest.mark.parametrize('plugin_root,platform,root_var,events', PLUGINS, ids=IDS)
def test_launcher_is_silent_when_the_cli_is_missing(
    plugin_root: Path, platform: str, root_var: str, events: set, tmp_path: Path,
) -> None:
    """A project without AgentDebugX installed must not see a hook failure."""
    result = subprocess.run(
        ['sh', str(plugin_root / 'bin' / 'agentdebug-hook'), '--platform', platform],
        input=json.dumps({'hook_event_name': 'Stop', 'cwd': str(tmp_path)}),
        capture_output=True, text=True,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)},
    )
    assert result.returncode == 0
    assert result.stdout == ''
    assert result.stderr == ''


@pytest.mark.parametrize('plugin_root,platform,root_var,events', PLUGINS, ids=IDS)
def test_launcher_never_reports_failure_for_bad_input(
    plugin_root: Path, platform: str, root_var: str, events: set, tmp_path: Path,
) -> None:
    launcher = str(plugin_root / 'bin' / 'agentdebug-hook')
    for payload in ('', 'not json', '{}', '{"hook_event_name": "Nonsense"}'):
        result = subprocess.run(
            ['sh', launcher, '--platform', platform],
            input=payload, capture_output=True, text=True,
        )
        assert result.returncode == 0, payload
        assert result.stderr == '', payload


@pytest.mark.parametrize('plugin_root,platform,root_var,events', PLUGINS, ids=IDS)
def test_precheck_skips_unconsented_projects_without_importing_agentdebug(
    plugin_root: Path, platform: str, root_var: str, events: set, tmp_path: Path,
) -> None:
    """0 dispatches, 1 skips; the probe must stay standard-library only."""
    precheck = str(plugin_root / 'bin' / 'precheck.py')
    host = {'claude': 'claude_code', 'codex': 'codex'}[platform]

    def probe(cwd: Path) -> int:
        return subprocess.run(
            [sys.executable, precheck, platform],
            input=json.dumps({'cwd': str(cwd)}),
            capture_output=True, text=True,
            env={'PATH': os.environ['PATH'], 'PYTHONPATH': ''},
        ).returncode

    assert probe(tmp_path) == 1

    config = tmp_path / '.agentdebug' / 'capture.json'
    config.parent.mkdir(parents=True)
    nested = tmp_path / 'a' / 'b'
    nested.mkdir(parents=True)

    config.write_text(json.dumps({
        'schema_version': 2, 'project_root': str(tmp_path),
        'store_path': str(tmp_path / '.agentdebug' / 'agentdebug.sqlite'),
        'platforms': {host: {'enabled': True, 'capture_events': []}},
    }))
    assert probe(tmp_path) == 0
    assert probe(nested) == 0, 'consent must be inherited from an ancestor'

    config.write_text(json.dumps({
        'schema_version': 2, 'project_root': str(tmp_path),
        'store_path': str(tmp_path / '.agentdebug' / 'agentdebug.sqlite'),
        'platforms': {host: {'enabled': False, 'capture_events': []}},
    }))
    assert probe(tmp_path) == 1

    other = 'codex' if host == 'claude_code' else 'claude_code'
    config.write_text(json.dumps({
        'schema_version': 2, 'project_root': str(tmp_path),
        'store_path': str(tmp_path / '.agentdebug' / 'agentdebug.sqlite'),
        'platforms': {other: {'enabled': True, 'capture_events': []}},
    }))
    assert probe(tmp_path) == 1, 'another host\'s consent must not apply'

    config.write_text('{ not json')
    assert probe(tmp_path) == 0, 'an unreadable config defers to the CLI'


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
