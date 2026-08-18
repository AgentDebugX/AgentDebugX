from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml  # type: ignore[import-untyped]


_SHA256_RE = re.compile(r'[0-9a-f]{64}')
_VERSION_RE = re.compile(r'\d+\.\d+\.\d+')
_HOME_PATH_RE = re.compile(r'~/[A-Za-z0-9._+/@-]+')
_DEFAULT_INSTALL_PATH = '~/.local/bin/claude'


@dataclass(frozen=True)
class PinnedClaudeArtifact:
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
    ) -> 'PinnedClaudeArtifact':
        artifact_path = Path(path).expanduser().resolve()
        if not artifact_path.is_file():
            raise FileNotFoundError(f'Claude artifact not found: {artifact_path}')
        if not _VERSION_RE.fullmatch(version):
            raise ValueError(f'invalid Claude version: {version!r}')

        expected_sha256 = expected_sha256.lower()
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError('expected SHA-256 must be 64 lowercase hex characters')

        digest_builder = hashlib.sha256()
        with artifact_path.open('rb') as artifact_file:
            header = artifact_file.read(20)
            digest_builder.update(header)
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b''):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        if (
            len(header) < 20
            or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != 62
        ):
            raise ValueError(
                f'Claude artifact is not a 64-bit little-endian x86-64 ELF: '
                f'{artifact_path}'
            )

        if digest != expected_sha256:
            raise ValueError(
                f'Claude artifact SHA-256 mismatch: expected {expected_sha256}, '
                f'got {digest}'
            )

        return cls(
            path=artifact_path,
            version=version,
            sha256=digest,
            size_bytes=artifact_path.stat().st_size,
        )


@dataclass(frozen=True)
class ClaudeInstallerConfig:
    artifact: PinnedClaudeArtifact
    install_path: str

    @classmethod
    def load(cls, path: Path | str) -> 'ClaudeInstallerConfig':
        config_path = Path(path).expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(f'Claude installer config not found: {config_path}')
        payload = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('Claude installer config must be a YAML mapping')

        allowed = {'version', 'artifact_path', 'artifact_sha256', 'install_path'}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f'unknown Claude installer config keys: {sorted(unknown)}')

        required = {'version', 'artifact_path', 'artifact_sha256'}
        missing = required - set(payload)
        if missing:
            raise ValueError(f'missing Claude installer config keys: {sorted(missing)}')

        version = payload['version']
        artifact_value = payload['artifact_path']
        expected_sha256 = payload['artifact_sha256']
        install_path = payload.get('install_path', _DEFAULT_INSTALL_PATH)
        for name, value in (
            ('version', version),
            ('artifact_path', artifact_value),
            ('artifact_sha256', expected_sha256),
            ('install_path', install_path),
        ):
            if not isinstance(value, str):
                raise ValueError(f'{name} must be a string')

        _validate_install_path(install_path)
        artifact_path = Path(artifact_value).expanduser()
        if not artifact_path.is_absolute():
            artifact_path = config_path.parent / artifact_path
        artifact = PinnedClaudeArtifact.load(
            artifact_path,
            version=version,
            expected_sha256=expected_sha256,
        )
        return cls(artifact=artifact, install_path=install_path)


def _validate_install_path(install_path: str) -> None:
    if not _HOME_PATH_RE.fullmatch(install_path):
        raise ValueError(
            'install_path must be a path below the agent home, such as '
            "'~/.local/bin/claude'"
        )
    relative = PurePosixPath(install_path.removeprefix('~/'))
    if '..' in relative.parts or relative.name in {'', '.', '..'}:
        raise ValueError('install_path must name a file below the agent home')


def _shell_home_path(path: str) -> str:
    _validate_install_path(path)
    return f'"$HOME/{path.removeprefix("~/")}"'


def build_install_command(
    artifact: PinnedClaudeArtifact,
    uploaded_path: str,
    *,
    install_path: str = _DEFAULT_INSTALL_PATH,
) -> str:
    source = shlex.quote(uploaded_path)
    version = artifact.version
    target = _shell_home_path(install_path)
    relative_parent = PurePosixPath(
        install_path.removeprefix('~/')
    ).parent
    target_dir = (
        '"$HOME"'
        if relative_parent == PurePosixPath('.')
        else _shell_home_path('~/' + str(relative_parent))
    )
    launcher = _shell_home_path(_DEFAULT_INSTALL_PATH)
    link_command = ''
    if install_path != _DEFAULT_INSTALL_PATH:
        link_command = f'mkdir -p "$HOME/.local/bin"; ln -sf {target} {launcher}; '
    return (
        'set -euo pipefail; '
        f'mkdir -p {target_dir}; '
        f'cp {source} {target}; '
        f'chmod 0755 {target}; '
        f'{link_command}'
        'printf "CLAUDE_ARTIFACT_INSTALLED\\n"; '
        'export PATH="$HOME/.local/bin:$PATH"; '
        'version_output="$(claude --version)"; '
        'printf "%s\\n" "$version_output"; '
        f'case "$version_output" in '
        f'"{version} (Claude Code)"|"claude v{version}") ;; '
        '*) printf "unexpected Claude version: %s\\n" "$version_output" >&2; '
        'exit 1 ;; esac'
    )
