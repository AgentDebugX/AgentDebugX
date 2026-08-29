import importlib.util
import io
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PLUGINS = [
    (
        ROOT / 'integrations' / 'claude-code' / 'plugins' / 'agentdebug',
        'claude_code',
        'claude',
    ),
    (
        ROOT / 'integrations' / 'codex' / 'plugins' / 'agentdebug',
        'codex',
        'codex',
    ),
]


@pytest.mark.parametrize('plugin_root,config_platform,cli_platform', PLUGINS)
def test_native_plugin_dispatches_only_for_enabled_projects(
    plugin_root: Path,
    config_platform: str,
    cli_platform: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    dispatch_path = plugin_root / 'hooks' / 'dispatch.py'
    spec = importlib.util.spec_from_file_location(
        f'agentdebug_{cli_platform}_plugin_dispatch', dispatch_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = json.dumps({'cwd': str(tmp_path / 'nested')})
    (tmp_path / 'nested').mkdir()
    calls = []
    monkeypatch.setattr(module.shutil, 'which', lambda command: '/usr/bin/uvx')
    monkeypatch.setattr(
        module.subprocess,
        'run',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    monkeypatch.setattr(module.sys, 'stdin', io.StringIO(payload))
    assert module.main() == 0
    assert calls == []

    config_path = tmp_path / '.agentdebug' / 'capture.json'
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                'platforms': {
                    config_platform: {
                        'enabled': True,
                        'installed_hooks': [],
                    }
                }
            }
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(module.sys, 'stdin', io.StringIO(payload))
    assert module.main() == 0
    assert len(calls) == 1
    command = calls[0][0][0]
    assert command[:4] == [
        '/usr/bin/uvx',
        '--quiet',
        '--from',
        'agentdebugx==0.4.0',
    ]
    assert command[-2:] == ['--platform', cli_platform]
    assert calls[0][1]['input'] == payload

    monkeypatch.setenv(
        'AGENTDEBUGX_PLUGIN_CLI', '/workspace/.venv/bin/agentdebug --verbose'
    )
    monkeypatch.setattr(module.sys, 'stdin', io.StringIO(payload))
    assert module.main() == 0
    assert calls[1][0][0][:2] == [
        '/workspace/.venv/bin/agentdebug',
        '--verbose',
    ]
