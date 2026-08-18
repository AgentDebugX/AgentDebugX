from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment

from examples.terminal_bench_eval.claude_artifact import (
    ClaudeInstallerConfig,
    PinnedClaudeArtifact,
    build_install_command,
)


class PinnedClaudeCode(ClaudeCode):
    """Harbor's Claude agent with only its installation mechanism replaced."""

    _UPLOADED_ARTIFACT = '/tmp/agentdebugx-claude-code'
    _METADATA_FILENAME = 'claude-install.json'

    def __init__(
        self,
        logs_dir: Path,
        *args: Any,
        claude_installer_config: str | None = None,
        claude_artifact_path: str | None = None,
        claude_artifact_sha256: str | None = None,
        claude_artifact_version: str | None = None,
        claude_install_path: str | None = None,
        claude_installer_config_source: str | None = None,
        **kwargs: Any,
    ) -> None:
        explicit_values = (
            claude_artifact_path,
            claude_artifact_sha256,
            claude_artifact_version,
            claude_install_path,
        )
        if claude_installer_config is not None:
            if any(value is not None for value in explicit_values):
                raise ValueError(
                    'claude_installer_config cannot be combined with explicit '
                    'artifact arguments'
                )
            config = ClaudeInstallerConfig.load(claude_installer_config)
            self._artifact = config.artifact
            self._install_path = config.install_path
        else:
            if (
                claude_artifact_path is None
                or claude_artifact_sha256 is None
                or claude_artifact_version is None
            ):
                raise ValueError(
                    'set claude_installer_config or all three explicit artifact '
                    'arguments'
                )
            self._artifact = PinnedClaudeArtifact.load(
                claude_artifact_path,
                version=claude_artifact_version,
                expected_sha256=claude_artifact_sha256,
            )
            self._install_path = claude_install_path or '~/.local/bin/claude'
        self._installer_config_source = claude_installer_config_source

        requested_version = kwargs.pop('version', None)
        if (
            requested_version is not None
            and requested_version != self._artifact.version
        ):
            raise ValueError(
                'requested version does not match the resolved Claude artifact'
            )
        super().__init__(
            logs_dir,
            *args,
            version=self._artifact.version,
            **kwargs,
        )

    def _write_install_metadata(self, **updates: Any) -> None:
        metadata = {
            'artifact_path': str(self._artifact.path),
            'artifact_sha256': self._artifact.sha256,
            'artifact_size_bytes': self._artifact.size_bytes,
            'expected_version': self._artifact.version,
            'installation_path': self._install_path,
            'installer_config_source': self._installer_config_source,
            **updates,
        }
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / self._METADATA_FILENAME
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)

    async def install(self, environment: BaseEnvironment) -> None:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        failure_class = 'agent_installation'
        try:
            await self._upload_agent_owned_file(
                environment,
                self._artifact.path,
                self._UPLOADED_ARTIFACT,
            )
            command = build_install_command(
                self._artifact,
                self._UPLOADED_ARTIFACT,
                install_path=self._install_path,
            )
            try:
                result = await self.exec_as_agent(environment, command=command)
            except Exception as exc:
                if 'CLAUDE_ARTIFACT_INSTALLED' in str(exc):
                    failure_class = 'artifact_compatibility'
                raise
            version_output = (result.stdout or '').strip().splitlines()[-1]
            installed_version = self.parse_version(version_output)
            self._write_install_metadata(
                status='success',
                failure_class=None,
                claude_version=installed_version,
                started_at=started_at.isoformat(),
                installation_time_seconds=round(time.monotonic() - started, 3),
            )
        except Exception as exc:
            self._write_install_metadata(
                status='failed',
                failure_class=failure_class,
                claude_version=None,
                error_type=type(exc).__name__,
                started_at=started_at.isoformat(),
                installation_time_seconds=round(time.monotonic() - started, 3),
            )
            raise
