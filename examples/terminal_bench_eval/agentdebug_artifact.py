from __future__ import annotations

import hashlib
import re
import shlex
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_SHA256_RE = re.compile(r'[0-9a-f]{64}')
_VERSION_RE = re.compile(r'\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?')
_DISTRIBUTION_NAME = 'agentdebugx'


@dataclass(frozen=True)
class PinnedAgentDebugArtifact:
    path: Path
    version: str
    sha256: str
    size_bytes: int

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        version: str,
        expected_sha256: str,
    ) -> 'PinnedAgentDebugArtifact':
        artifact_path = Path(path).expanduser().resolve()
        if not artifact_path.is_file():
            raise FileNotFoundError(f'AgentDebugX wheel not found: {artifact_path}')
        if artifact_path.suffix != '.whl':
            raise ValueError(f'AgentDebugX artifact must be a wheel: {artifact_path}')
        if not _VERSION_RE.fullmatch(version):
            raise ValueError(f'invalid AgentDebugX version: {version!r}')

        expected_sha256 = expected_sha256.lower()
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError('expected SHA-256 must be 64 lowercase hex characters')

        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            raise ValueError(
                'AgentDebugX wheel SHA-256 mismatch: '
                f'expected {expected_sha256}, got {digest}'
            )

        metadata = _read_wheel_metadata(artifact_path)
        if metadata.get('Name', '').lower() != _DISTRIBUTION_NAME:
            raise ValueError(
                'AgentDebugX wheel has unexpected distribution name: '
                f"{metadata.get('Name')!r}"
            )
        if metadata.get('Version') != version:
            raise ValueError(
                'AgentDebugX wheel version mismatch: '
                f"expected {version!r}, got {metadata.get('Version')!r}"
            )

        return cls(
            path=artifact_path,
            version=version,
            sha256=digest,
            size_bytes=artifact_path.stat().st_size,
        )


@dataclass(frozen=True)
class AgentDebugInstallerConfig:
    artifact: PinnedAgentDebugArtifact

    @classmethod
    def load(cls, path: Path | str) -> 'AgentDebugInstallerConfig':
        config_path = Path(path).expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(
                f'AgentDebugX installer config not found: {config_path}'
            )
        payload = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('AgentDebugX installer config must be a YAML mapping')

        allowed = {'version', 'artifact_path', 'artifact_sha256'}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                f'unknown AgentDebugX installer config keys: {sorted(unknown)}'
            )
        missing = allowed - set(payload)
        if missing:
            raise ValueError(
                f'missing AgentDebugX installer config keys: {sorted(missing)}'
            )

        version = payload['version']
        artifact_value = payload['artifact_path']
        expected_sha256 = payload['artifact_sha256']
        for name, value in (
            ('version', version),
            ('artifact_path', artifact_value),
            ('artifact_sha256', expected_sha256),
        ):
            if not isinstance(value, str):
                raise ValueError(f'{name} must be a string')

        artifact_path = Path(artifact_value).expanduser()
        if not artifact_path.is_absolute():
            artifact_path = config_path.parent / artifact_path
        return cls(
            artifact=PinnedAgentDebugArtifact.load(
                artifact_path,
                version=version,
                expected_sha256=expected_sha256,
            )
        )


def _read_wheel_metadata(path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as wheel:
            metadata_names = [
                name
                for name in wheel.namelist()
                if name.endswith('.dist-info/METADATA')
            ]
            if len(metadata_names) != 1:
                raise ValueError(
                    'AgentDebugX wheel must contain exactly one dist-info/METADATA '
                    f'file; found {len(metadata_names)}'
                )
            text = wheel.read(metadata_names[0]).decode('utf-8')
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f'invalid AgentDebugX wheel {path}: {exc}') from exc

    metadata: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(':')
        if separator and key in {'Name', 'Version'}:
            metadata[key] = value.strip()
    return metadata


def build_install_command(
    artifact: PinnedAgentDebugArtifact,
    uploaded_path: str,
) -> str:
    source = shlex.quote(uploaded_path)
    expected_version = shlex.quote(artifact.version)
    return (
        'set -euo pipefail; '
        f'python3 -m pip install --disable-pip-version-check --no-input '
        f'--no-cache-dir --user {source}; '
        'export PATH="$HOME/.local/bin:$PATH"; '
        "installed_version=\"$(python3 -c 'import agentdebug; "
        "print(agentdebug.__version__)')\"; "
        f'[ "$installed_version" = {expected_version} ]; '
        'command -v agentdebug >/dev/null; '
        'printf "AGENTDEBUGX_ARTIFACT_INSTALLED\\n%s\\n" "$installed_version"'
    )
