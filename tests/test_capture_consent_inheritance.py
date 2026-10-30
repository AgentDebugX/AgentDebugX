"""A host's nearest explicit consent overrides an ancestor's settings."""

from pathlib import Path

import pytest

from agentdebug.capture.config import disable_capture
from agentdebug.capture.hosts.codex import CodexCaptureHost
from agentdebug.cli.commands.capture import _find_enabled_project
from agentdebug.integrations.capture_management import enable_capture_consent


@pytest.mark.parametrize('platform,host', [('claude', 'claude_code'), ('codex', 'codex')])
def test_dispatch_stops_at_disabled_child(tmp_path: Path, platform: str, host: str) -> None:
    child = tmp_path / 'child'
    nested = child / 'nested'
    nested.mkdir(parents=True)
    enable_capture_consent(platform, tmp_path)
    enable_capture_consent(platform, child)
    disable_capture(child, host)

    assert _find_enabled_project(nested, host) is None
    assert _find_enabled_project(tmp_path, host) == tmp_path


def test_codex_current_context_stops_at_disabled_child(tmp_path: Path) -> None:
    child = tmp_path / 'child'
    child.mkdir()
    enable_capture_consent('codex', tmp_path)
    enable_capture_consent('codex', child)
    disable_capture(child, 'codex')

    with pytest.raises(ValueError, match='automatic capture is not enabled'):
        CodexCaptureHost().resolve_current_context({'CODEX_THREAD_ID': 'session-1'}, child)
    assert not (tmp_path / '.agentdebug' / 'agentdebug.sqlite').exists()


@pytest.mark.parametrize('platform,host,other', [
    ('claude', 'claude_code', 'codex'), ('codex', 'codex', 'claude'),
])
def test_another_hosts_config_still_inherits_parent(
    tmp_path: Path, platform: str, host: str, other: str,
) -> None:
    child = tmp_path / 'child'
    child.mkdir()
    enable_capture_consent(platform, tmp_path)
    enable_capture_consent(other, child)

    assert _find_enabled_project(child, host) == tmp_path
