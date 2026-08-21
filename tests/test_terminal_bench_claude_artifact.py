import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.terminal_bench_eval.claude_artifact import (
    ClaudeInstallerConfig,
    PinnedClaudeArtifact,
    build_install_command,
)
from examples.terminal_bench_eval.install_matrix import build_install_matrix
from examples.terminal_bench_eval.run_eval import (
    _build_run_command,
    _loggable_command,
    _resolved_installer_kwargs,
    build_parser,
)


def _write_x86_64_elf(path: Path) -> None:
    header = bytearray(64)
    header[:4] = b'\x7fELF'
    header[4] = 2
    header[5] = 1
    header[18:20] = (62).to_bytes(2, 'little')
    path.write_bytes(header)
    path.chmod(0o755)


def test_pinned_artifact_rejects_a_hash_mismatch(tmp_path: Path) -> None:
    artifact_path = tmp_path / 'claude'
    _write_x86_64_elf(artifact_path)

    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        PinnedClaudeArtifact.load(
            artifact_path,
            version='2.1.233',
            expected_sha256='0' * 64,
        )


def test_yaml_config_selects_the_artifact_version_and_install_path(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / 'artifacts' / 'claude'
    artifact_path.parent.mkdir()
    _write_x86_64_elf(artifact_path)
    config_path = tmp_path / 'claude-installer.yaml'
    config_path.write_text(
        '''version: 2.1.233
artifact_path: artifacts/claude
artifact_sha256: 175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6
install_path: ~/tools/claude-2.1.233
'''
    )

    config = ClaudeInstallerConfig.load(config_path)
    command = build_install_command(
        config.artifact,
        '/tmp/uploaded-claude',
        install_path=config.install_path,
    )

    assert config.artifact.path == artifact_path.resolve()
    assert config.artifact.version == '2.1.233'
    assert config.install_path == '~/tools/claude-2.1.233'
    assert '"$HOME/tools/claude-2.1.233"' in command
    assert 'ln -sf' in command


def test_install_command_supports_a_file_directly_below_home(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / 'claude'
    _write_x86_64_elf(artifact_path)
    artifact = PinnedClaudeArtifact.load(
        artifact_path,
        version='2.1.233',
        expected_sha256=(
            '175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6'
        ),
    )

    command = build_install_command(
        artifact,
        '/tmp/uploaded-claude',
        install_path='~/claude',
    )

    assert 'mkdir -p "$HOME"' in command
    assert 'cp /tmp/uploaded-claude "$HOME/claude"' in command


def test_runner_snapshots_resolved_yaml_values_into_agent_kwargs(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / 'claude'
    _write_x86_64_elf(artifact_path)
    config_path = tmp_path / 'claude-installer.yaml'
    config_path.write_text(
        '''version: 2.1.233
artifact_path: claude
artifact_sha256: 175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6
install_path: ~/claude
'''
    )

    kwargs = _resolved_installer_kwargs(config_path)

    assert kwargs == [
        f'claude_artifact_path={artifact_path.resolve()}',
        'claude_artifact_sha256='
        '175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6',
        'claude_artifact_version=2.1.233',
        'claude_install_path=~/claude',
        f'claude_installer_config_source={config_path.resolve()}',
    ]


def test_install_command_uses_only_the_agent_home_and_pinned_artifact(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / 'claude'
    _write_x86_64_elf(artifact_path)
    artifact = PinnedClaudeArtifact.load(
        artifact_path,
        version='2.1.233',
        expected_sha256=(
            '175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6'
        ),
    )

    command = build_install_command(artifact, '/tmp/claude-code-2.1.233')

    assert 'mkdir -p "$HOME/.local/bin"' in command
    assert '"$HOME/.local/bin/claude"' in command
    assert 'claude --version' in command
    assert '"2.1.233 (Claude Code)"|"claude v2.1.233"' in command
    assert 'grep' not in command
    for forbidden in ('apt-get', 'npm', 'curl', '/etc'):
        assert forbidden not in command


def test_install_matrix_distinguishes_success_from_image_start_failure(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / 'job'
    cache_dir = tmp_path / 'sif-cache'
    cache_dir.mkdir()
    success = job_dir / 'known-good__abc'
    (success / 'agent').mkdir(parents=True)
    (success / 'result.json').write_text(
        json.dumps(
            {
                'task_name': 'terminal-bench/known-good',
                'exception_info': None,
            }
        )
    )
    (success / 'agent' / 'claude-install.json').write_text(
        json.dumps(
            {
                'status': 'success',
                'claude_version': '2.1.233',
                'artifact_sha256': 'a' * 64,
                'installation_time_seconds': 1.25,
                'failure_class': None,
            }
        )
    )
    (cache_dir / 'alexgshaw_known-good_20251031.sif').touch()

    failed = job_dir / 'conversion-timeout__def'
    failed.mkdir()
    (failed / 'result.json').write_text(
        json.dumps(
            {
                'task_name': 'terminal-bench/conversion-timeout',
                'exception_info': {
                    'exception_type': 'RuntimeError',
                    'exception_message': 'Server process died on port 12345.',
                },
                'config': {
                    'agent': {
                        'kwargs': {
                            'claude_artifact_sha256': (
                                '175f7c04d8aba899227b47bb38b83e24dff845172c703589e'
                                '27b398b6d4989f6'
                            ),
                        }
                    }
                },
            }
        )
    )

    configuration_error = job_dir / 'z-configuration-error__ghi'
    configuration_error.mkdir()
    (configuration_error / 'result.json').write_text(
        json.dumps(
            {
                'task_name': 'terminal-bench/z-configuration-error',
                'exception_info': {
                    'exception_type': 'ValueError',
                    'exception_message': 'Invalid custom agent configuration.',
                },
            }
        )
    )

    rows = build_install_matrix(job_dir, cache_dir)

    assert rows[0] == {
        'task': 'terminal-bench/conversion-timeout',
        'sif_status': 'missing',
        'sif_path': None,
        'status': 'failed',
        'claude_version': None,
        'artifact_sha256': (
            '175f7c04d8aba899227b47bb38b83e24dff845172c703589e27b398b6d4989f6'
        ),
        'installation_time_seconds': None,
        'failure_class': 'image_start_or_conversion',
        'error_type': 'RuntimeError',
    }
    assert rows[1]['task'] == 'terminal-bench/known-good'
    assert rows[1]['sif_status'] == 'cached'
    assert rows[1]['status'] == 'success'
    assert rows[1]['artifact_sha256'] == 'a' * 64
    assert rows[1]['installation_time_seconds'] == 1.25
    assert rows[2]['failure_class'] == 'agent_installation'


def test_run_command_omits_resume_flags_for_a_seed_attempt() -> None:
    parser = build_parser()
    args = parser.parse_args([
        'run', '--method', 'seed', '--task', 'sqlite-db-truncate',
        '--agent', 'claude-code', '--jobs-dir', 'jobs', '--sif-cache-dir', 'sif',
    ])

    cmd = _build_run_command(args, ['terminal-bench/sqlite-db-truncate'])

    assert '--load-trajectory' not in cmd
    assert '--mounts' not in cmd
    assert '--agent-env' not in cmd


def test_run_command_carries_recovery_method_instruction_through_to_harbor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('AGENTDEBUG_LLM_BASE_URL', 'https://proxy.example/v1')
    monkeypatch.setenv('AGENTDEBUG_LLM_API_KEY', 's3cr3t')
    parser = build_parser()
    args = parser.parse_args([
        'run', '--method', 'rerun-deep', '--task', 'sqlite-db-truncate',
        '--agent', 'claude-code', '--jobs-dir', 'jobs', '--sif-cache-dir', 'sif',
        '--load-trajectory', '/mnt/prior/seed.jsonl',
        '--mount', '/host/diagnostic-input/seed.jsonl:/mnt/agentdebug-input/failed.jsonl',
        '--agent-env', 'AGENTDEBUG_LLM_BASE_URL',
        '--agent-env', 'AGENTDEBUG_LLM_API_KEY',
    ])

    cmd = _build_run_command(args, ['terminal-bench/sqlite-db-truncate'])

    assert cmd[cmd.index('--load-trajectory') + 1] == '/mnt/prior/seed.jsonl'
    mount_indexes = [i for i, value in enumerate(cmd) if value == '--mounts']
    assert len(mount_indexes) == 1
    mounts = json.loads(cmd[mount_indexes[0] + 1])
    assert mounts == [{
        'type': 'bind',
        'source': '/host/diagnostic-input/seed.jsonl',
        'target': '/mnt/agentdebug-input/failed.jsonl',
        'read_only': True,
    }]
    agent_env_indexes = [i for i, value in enumerate(cmd) if value == '--agent-env']
    assert [cmd[i + 1] for i in agent_env_indexes] == [
        'AGENTDEBUG_LLM_BASE_URL=https://proxy.example/v1',
        'AGENTDEBUG_LLM_API_KEY=s3cr3t',
    ]


def test_run_command_rejects_an_unset_agent_env_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('AGENTDEBUG_LLM_API_KEY', raising=False)
    parser = build_parser()
    args = parser.parse_args([
        'run', '--method', 'rerun-deep', '--task', 'sqlite-db-truncate',
        '--agent', 'claude-code', '--jobs-dir', 'jobs', '--sif-cache-dir', 'sif',
        '--agent-env', 'AGENTDEBUG_LLM_API_KEY',
    ])

    with pytest.raises(ValueError, match='AGENTDEBUG_LLM_API_KEY is not set'):
        _build_run_command(args, ['terminal-bench/sqlite-db-truncate'])


def test_loggable_command_masks_agent_env_values() -> None:
    cmd = ['harbor', 'run', '--agent-env', 'AGENTDEBUG_LLM_API_KEY=s3cr3t', '-a', 'claude-code']

    rendered = _loggable_command(cmd)

    assert 's3cr3t' not in rendered
    assert 'AGENTDEBUG_LLM_API_KEY=***' in rendered


def test_install_matrix_cli_can_run_as_a_script() -> None:
    script = (
        Path(__file__).parents[1]
        / 'examples'
        / 'terminal_bench_eval'
        / 'install_matrix.py'
    )

    result = subprocess.run(
        [sys.executable, str(script), '--help'],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert '--sif-cache-dir' in result.stdout
