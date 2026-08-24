import hashlib
import zipfile
from pathlib import Path

import pytest

from examples.terminal_bench_eval.agentdebug_artifact import (
    AgentDebugInstallerConfig,
    PinnedAgentDebugArtifact,
    build_install_command,
)
from examples.terminal_bench_eval.run_eval import (
    AGENTDEBUG_CLAUDE_AGENT,
    _build_run_command,
    build_parser,
)


def _write_wheel(
    path: Path,
    *,
    name: str = 'agentdebugx',
    version: str = '0.3.1',
) -> str:
    with zipfile.ZipFile(path, 'w') as wheel:
        wheel.writestr(
            f'{name}-{version}.dist-info/METADATA',
            f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_x86_64_elf(path: Path) -> str:
    header = bytearray(64)
    header[:4] = b'\x7fELF'
    header[4] = 2
    header[5] = 1
    header[18:20] = (62).to_bytes(2, 'little')
    path.write_bytes(header)
    path.chmod(0o755)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pinned_agentdebug_wheel_validates_hash_name_and_version(
    tmp_path: Path,
) -> None:
    wheel_path = tmp_path / 'agentdebugx-0.3.1-py3-none-any.whl'
    digest = _write_wheel(wheel_path)

    artifact = PinnedAgentDebugArtifact.load(
        wheel_path,
        version='0.3.1',
        expected_sha256=digest,
    )

    assert artifact.path == wheel_path.resolve()
    assert artifact.version == '0.3.1'
    assert artifact.sha256 == digest


def test_pinned_agentdebug_wheel_rejects_a_hash_mismatch(tmp_path: Path) -> None:
    wheel_path = tmp_path / 'agentdebugx-0.3.1-py3-none-any.whl'
    _write_wheel(wheel_path)

    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        PinnedAgentDebugArtifact.load(
            wheel_path,
            version='0.3.1',
            expected_sha256='0' * 64,
        )


def test_agentdebug_installer_config_resolves_a_relative_wheel(
    tmp_path: Path,
) -> None:
    wheel_path = tmp_path / 'artifacts' / 'agentdebugx-0.3.1-py3-none-any.whl'
    wheel_path.parent.mkdir()
    digest = _write_wheel(wheel_path)
    config_path = tmp_path / 'agentdebug-installer.yaml'
    config_path.write_text(
        'version: 0.3.1\n'
        'artifact_path: artifacts/agentdebugx-0.3.1-py3-none-any.whl\n'
        f'artifact_sha256: {digest}\n'
    )

    config = AgentDebugInstallerConfig.load(config_path)

    assert config.artifact.path == wheel_path.resolve()
    assert config.artifact.sha256 == digest


def test_agentdebug_install_command_uses_python_user_install_and_doctor_is_separate(
    tmp_path: Path,
) -> None:
    wheel_path = tmp_path / 'agentdebugx-0.3.1-py3-none-any.whl'
    digest = _write_wheel(wheel_path)
    artifact = PinnedAgentDebugArtifact.load(
        wheel_path,
        version='0.3.1',
        expected_sha256=digest,
    )

    command = build_install_command(
        artifact,
        '/tmp/agentdebugx-0.3.1-py3-none-any.whl',
    )

    assert 'python3 -m pip install' in command
    assert '--user /tmp/agentdebugx-0.3.1-py3-none-any.whl' in command
    assert 'command -v agentdebug' in command
    assert "agentdebug.__version__" in command
    assert 'agentdebug doctor' not in command
    for forbidden in ('apt-get', 'npm', 'curl', 'prune', '--rmi'):
        assert forbidden not in command


def _write_installer_configs(tmp_path: Path) -> tuple[Path, Path]:
    claude_path = tmp_path / 'claude'
    claude_digest = _write_x86_64_elf(claude_path)
    claude_config = tmp_path / 'claude-installer.yaml'
    claude_config.write_text(
        'version: 2.1.233\n'
        'artifact_path: claude\n'
        f'artifact_sha256: {claude_digest}\n'
        'install_path: ~/.local/bin/claude\n'
    )

    wheel_path = tmp_path / 'agentdebugx-0.3.1-py3-none-any.whl'
    wheel_digest = _write_wheel(wheel_path)
    agentdebug_config = tmp_path / 'agentdebug-installer.yaml'
    agentdebug_config.write_text(
        'version: 0.3.1\n'
        f'artifact_path: {wheel_path.name}\n'
        f'artifact_sha256: {wheel_digest}\n'
    )
    return claude_config, agentdebug_config


def test_agentdebug_config_selects_the_custom_agent_for_docker(
    tmp_path: Path,
) -> None:
    claude_config, agentdebug_config = _write_installer_configs(tmp_path)
    parser = build_parser()
    args = parser.parse_args([
        'run', '--method', 'provision-agentdebug',
        '--task', 'sqlite-db-truncate', '--jobs-dir', 'jobs',
        '--environment-backend', 'docker', '--install-only',
        '--claude-installer-config', str(claude_config),
        '--agentdebug-installer-config', str(agentdebug_config),
    ])

    cmd = _build_run_command(args, ['terminal-bench/sqlite-db-truncate'])

    assert cmd[cmd.index('-a') + 1] == AGENTDEBUG_CLAUDE_AGENT
    assert '--no-delete' in cmd
    assert '--install-only' in cmd
    assert any(value.startswith('agentdebug_wheel_path=') for value in cmd)
    assert any(value.startswith('agentdebug_wheel_sha256=') for value in cmd)
    assert 'agentdebug_version=0.3.1' in cmd
    assert not any(value in cmd for value in ('prune', '--rmi', '--volumes'))


def test_agentdebug_config_rejects_non_docker_backends(tmp_path: Path) -> None:
    claude_config, agentdebug_config = _write_installer_configs(tmp_path)
    parser = build_parser()
    args = parser.parse_args([
        'run', '--method', 'provision-agentdebug',
        '--task', 'sqlite-db-truncate', '--jobs-dir', 'jobs',
        '--environment-backend', 'singularity', '--sif-cache-dir', 'sif',
        '--claude-installer-config', str(claude_config),
        '--agentdebug-installer-config', str(agentdebug_config),
    ])

    with pytest.raises(ValueError, match='only Docker'):
        _build_run_command(args, ['terminal-bench/sqlite-db-truncate'])
