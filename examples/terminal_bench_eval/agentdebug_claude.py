from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.environments.docker.docker import DockerEnvironment

from examples.terminal_bench_eval.agentdebug_artifact import (
    AgentDebugInstallerConfig,
    PinnedAgentDebugArtifact,
    build_install_command,
)
from examples.terminal_bench_eval.pinned_claude import PinnedClaudeCode


class AgentDebugClaudeCode(PinnedClaudeCode):
    """Pinned Claude Code with a pinned AgentDebugX CLI provisioned at setup."""

    _AGENTDEBUG_METADATA_FILENAME = 'agentdebug-install.json'

    def __init__(
        self,
        logs_dir: Path,
        *args: Any,
        agentdebug_installer_config: str | None = None,
        agentdebug_wheel_path: str | None = None,
        agentdebug_wheel_sha256: str | None = None,
        agentdebug_version: str | None = None,
        agentdebug_installer_config_source: str | None = None,
        **kwargs: Any,
    ) -> None:
        explicit_values = (
            agentdebug_wheel_path,
            agentdebug_wheel_sha256,
            agentdebug_version,
        )
        if agentdebug_installer_config is not None:
            if any(value is not None for value in explicit_values):
                raise ValueError(
                    'agentdebug_installer_config cannot be combined with '
                    'explicit AgentDebugX artifact arguments'
                )
            config = AgentDebugInstallerConfig.load(agentdebug_installer_config)
            self._agentdebug_artifact = config.artifact
        else:
            if any(value is None for value in explicit_values):
                raise ValueError(
                    'set agentdebug_installer_config or all three explicit '
                    'AgentDebugX artifact arguments'
                )
            self._agentdebug_artifact = PinnedAgentDebugArtifact.load(
                agentdebug_wheel_path,  # type: ignore[arg-type]
                version=agentdebug_version,  # type: ignore[arg-type]
                expected_sha256=agentdebug_wheel_sha256,  # type: ignore[arg-type]
            )
        self._agentdebug_installer_config_source = (
            agentdebug_installer_config_source
        )
        super().__init__(logs_dir, *args, **kwargs)

    def _write_agentdebug_metadata(self, **updates: Any) -> None:
        metadata = {
            'artifact_path': str(self._agentdebug_artifact.path),
            'artifact_sha256': self._agentdebug_artifact.sha256,
            'artifact_size_bytes': self._agentdebug_artifact.size_bytes,
            'expected_version': self._agentdebug_artifact.version,
            'installer_config_source': self._agentdebug_installer_config_source,
            **updates,
        }
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / self._AGENTDEBUG_METADATA_FILENAME
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)

    async def install(self, environment: BaseEnvironment) -> None:
        if not isinstance(environment, DockerEnvironment):
            raise RuntimeError(
                'AgentDebugClaudeCode currently supports only Harbor Docker environments'
            )

        await super().install(environment)

        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        failure_class = 'agentdebug_installation'
        try:
            uploaded_wheel = f'/tmp/{self._agentdebug_artifact.path.name}'
            await self._upload_agent_owned_file(
                environment,
                self._agentdebug_artifact.path,
                uploaded_wheel,
            )
            await self.exec_as_agent(
                environment,
                command=build_install_command(
                    self._agentdebug_artifact,
                    uploaded_wheel,
                ),
                timeout_sec=300,
            )
            failure_class = 'agentdebug_doctor'
            await self.exec_as_agent(
                environment,
                command=(
                    'set -euo pipefail; '
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    'agentdebug doctor'
                ),
                timeout_sec=60,
            )
            self._write_agentdebug_metadata(
                status='success',
                failure_class=None,
                installed_version=self._agentdebug_artifact.version,
                doctor_status='success',
                started_at=started_at.isoformat(),
                installation_time_seconds=round(time.monotonic() - started, 3),
            )
        except Exception as exc:
            self._write_agentdebug_metadata(
                status='failed',
                failure_class=failure_class,
                installed_version=None,
                doctor_status=(
                    'failed' if failure_class == 'agentdebug_doctor' else 'not_run'
                ),
                error_type=type(exc).__name__,
                started_at=started_at.isoformat(),
                installation_time_seconds=round(time.monotonic() - started, 3),
            )
            raise
