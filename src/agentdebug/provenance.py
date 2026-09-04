"""Where this copy of AgentDebugX came from, for consumers that stamp rows.

A row produced by a diagnosis is only reproducible if it records which
AgentDebugX produced it. The distribution version is not enough: an editable
install tracks a git checkout whose content moves while the version string
stays put. ``provenance()`` therefore reports the installed version and, when
the package is imported from a git checkout, the commit it is at and whether
that checkout has uncommitted changes. Everything is read locally and never
raises: a consumer stamping a million rows must not fail because ``git`` is
missing on the host.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentdebug import __version__

PACKAGE = 'agentdebugx'


def _git(args: List[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ['git', *args], cwd=str(cwd), capture_output=True, text=True, timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _checkout_root(start: Path) -> Optional[Path]:
    for candidate in (start, *start.parents):
        if (candidate / '.git').exists():
            return candidate
    return None


def provenance(package_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Return ``{"package", "version", "git_sha", "git_dirty", "python", "platform"}``.

    ``git_sha`` and ``git_dirty`` are ``None`` unless the package is imported
    from inside a git checkout and ``git`` answers; ``git_dirty`` is ``True``
    when that checkout has uncommitted changes under the package directory.
    """
    here = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    root = _checkout_root(here)
    sha = _git(['rev-parse', 'HEAD'], root) if root else None
    dirty: Optional[bool] = None
    if root and sha:
        status = _git(['status', '--porcelain', '--untracked-files=no', '--', str(here)], root)
        dirty = bool(status)
    return {
        'package': PACKAGE,
        'version': __version__,
        'git_sha': sha,
        'git_dirty': dirty,
        'python': sys.version.split()[0],
        'platform': platform.platform(),
    }


__all__ = ['provenance', 'PACKAGE']
