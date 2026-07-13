"""Concrete Hub backends: LocalHub, GitHub, HuggingFaceHub.

Each backend implements the :class:`~agentdebug.hub.backend_base.HubBackend`
Protocol. Use :func:`backend_from_spec` to construct one from a string
identifier (``local:/...``, ``git:<url>[#path]``, ``hf:<repo_id>[#path]``).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from agentdebug.hub.backend_base import HubBackend, HubSpec, parse_spec
from agentdebug.hub.bundle import Bundle, pack_bundle, unpack_bundle

LOG = logging.getLogger('agentdebug.hub')


# ---------------- Local ---------------- #

class LocalHubBackend:
    """A hub that is just a directory on the local filesystem.

    Use for tests, air-gapped environments, or a shared NFS mount.
    """

    scheme = 'local'

    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def push(self, bundle: Bundle, *, message: Optional[str] = None) -> str:
        path = pack_bundle(bundle, self.root)
        return str(path)

    def pull(self, bundle_id: str, *, into: Path) -> Path:
        src = self.root / bundle_id
        if not src.is_dir():
            raise FileNotFoundError(f'No bundle {bundle_id!r} at {self.root}')
        dst = into / bundle_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return dst

    def list_bundles(self, *, limit: int = 100) -> List[str]:
        return sorted(
            (p.name for p in self.root.iterdir() if p.is_dir()),
            reverse=True,
        )[:limit]


# ---------------- Git ---------------- #

class GitHubBackend:
    """Push/pull bundles to/from any Git remote via the ``git`` CLI.

    Works against GitHub, GitLab, Gitea, Bitbucket, or self-hosted git over
    SSH/HTTPS. Authentication is whatever your local ``git`` is configured
    to do (SSH key, credential helper, env-based token, etc.) — AgentDebugX
    deliberately does not handle credentials.

    The backend shallow-clones the remote into a temp dir, copies the bundle
    under ``<subpath>/``, commits, and pushes. ``pull`` does the reverse.
    """

    scheme = 'git'

    def __init__(
        self,
        remote: str,
        *,
        subpath: str = 'bundles',
        branch: str = 'main',
    ) -> None:
        self.remote = remote
        self.subpath = subpath.strip('/')
        self.branch = branch

    @classmethod
    def from_spec(cls, spec: HubSpec) -> 'GitHubBackend':
        return cls(
            remote=spec.location,
            subpath=spec.subpath or 'bundles',
        )

    def push(self, bundle: Bundle, *, message: Optional[str] = None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            self._clone(repo)
            target_dir = repo / self.subpath
            target_dir.mkdir(parents=True, exist_ok=True)
            pack_bundle(bundle, target_dir)
            msg = message or (
                f'agentdebug hub: add bundle {bundle.manifest.bundle_id}'
                + (f' ({bundle.manifest.framework})'
                   if bundle.manifest.framework else '')
            )
            self._run(['git', 'add', '.'], cwd=repo)
            self._run(['git', 'commit', '-m', msg], cwd=repo)
            self._run(['git', 'push', 'origin', self.branch], cwd=repo)
        return f'{self.remote}#{self.subpath}/{bundle.manifest.bundle_id}'

    def pull(self, bundle_id: str, *, into: Path) -> Path:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            self._clone(repo)
            src = repo / self.subpath / bundle_id
            if not src.is_dir():
                raise FileNotFoundError(
                    f'No bundle {bundle_id!r} under {self.subpath}/ in {self.remote}'
                )
            dst = into / bundle_id
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            return dst

    def list_bundles(self, *, limit: int = 100) -> List[str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            self._clone(repo)
            base = repo / self.subpath
            if not base.is_dir():
                return []
            return sorted(
                (p.name for p in base.iterdir() if p.is_dir()),
                reverse=True,
            )[:limit]

    # ---- helpers ----

    def _clone(self, dest: Path) -> None:
        self._run(
            ['git', 'clone', '--depth', '1', '--branch', self.branch,
             self.remote, str(dest)]
        )

    def _run(self, cmd: List[str], *, cwd: Optional[Path] = None) -> None:
        LOG.debug('git: %s', ' '.join(cmd))
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed (exit {result.returncode}): "
                f"{' '.join(cmd)}\n--- stderr ---\n{result.stderr}"
            )


# ---------------- Hugging Face ---------------- #

class HuggingFaceBackend:
    """Push/pull bundles to/from a Hugging Face Datasets repo.

    Requires the optional ``huggingface_hub`` package
    (``pip install 'agentdebugx[hub-hf]'``). Authentication uses
    ``HF_TOKEN`` or ``huggingface-cli login`` — same as the rest of the HF
    ecosystem.
    """

    scheme = 'hf'

    def __init__(self, repo_id: str, *, subpath: str = 'bundles') -> None:
        self.repo_id = repo_id
        self.subpath = subpath.strip('/')

    @classmethod
    def from_spec(cls, spec: HubSpec) -> 'HuggingFaceBackend':
        return cls(repo_id=spec.location, subpath=spec.subpath or 'bundles')

    def _api(self) -> 'object':
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                'HuggingFaceBackend requires `huggingface_hub`. '
                "Install with `pip install 'agentdebugx[hub-hf]'`."
            ) from exc
        return HfApi()

    def push(self, bundle: Bundle, *, message: Optional[str] = None) -> str:
        api = self._api()
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / self.subpath / bundle.manifest.bundle_id
            staging.parent.mkdir(parents=True, exist_ok=True)
            packed_root = staging.parent
            pack_bundle(bundle, packed_root)
            commit_message = message or (
                f'agentdebug hub: add bundle {bundle.manifest.bundle_id}'
            )
            api.upload_folder(  # type: ignore[attr-defined]
                folder_path=str(packed_root),
                repo_id=self.repo_id,
                repo_type='dataset',
                path_in_repo=self.subpath,
                commit_message=commit_message,
            )
        return f'https://huggingface.co/datasets/{self.repo_id}'

    def pull(self, bundle_id: str, *, into: Path) -> Path:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                'HuggingFaceBackend requires `huggingface_hub`. '
                "Install with `pip install 'agentdebugx[hub-hf]'`."
            ) from exc
        local = Path(
            snapshot_download(
                repo_id=self.repo_id,
                repo_type='dataset',
                allow_patterns=[
                    f'{self.subpath}/{bundle_id}/*',
                    f'{self.subpath}/{bundle_id}/**/*',
                ],
            )
        )
        src = local / self.subpath / bundle_id
        if not src.is_dir():
            raise FileNotFoundError(
                f'No bundle {bundle_id!r} in {self.repo_id} under {self.subpath}/'
            )
        dst = into / bundle_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return dst

    def list_bundles(self, *, limit: int = 100) -> List[str]:
        api = self._api()
        files = api.list_repo_files(  # type: ignore[attr-defined]
            repo_id=self.repo_id, repo_type='dataset'
        )
        ids: List[str] = []
        prefix = f'{self.subpath}/'
        for f in files:
            if not f.startswith(prefix):
                continue
            tail = f[len(prefix):]
            head = tail.split('/', 1)[0]
            if head and head not in ids:
                ids.append(head)
        return sorted(ids, reverse=True)[:limit]


# ---------------- Spec dispatch ---------------- #

def backend_from_spec(spec: str | HubSpec) -> HubBackend:
    """Resolve a hub identifier (string or parsed) to a backend instance."""
    parsed = spec if isinstance(spec, HubSpec) else parse_spec(spec)
    if parsed.scheme == 'local':
        return LocalHubBackend(parsed.location)
    if parsed.scheme == 'git':
        return GitHubBackend.from_spec(parsed)
    if parsed.scheme == 'hf':
        return HuggingFaceBackend.from_spec(parsed)
    raise ValueError(f'Unsupported hub scheme: {parsed.scheme!r}')


__all__ = [
    'GitHubBackend',
    'HuggingFaceBackend',
    'LocalHubBackend',
    'backend_from_spec',
]
