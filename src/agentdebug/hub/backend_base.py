"""Hub backend Protocol + spec parser.

A backend implements push / pull / list against some remote (local dir, git
remote, HF dataset, …). Concrete implementations live in
:mod:`agentdebug.hub.backends`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from agentdebug.hub.bundle import Bundle


@dataclass(frozen=True)
class HubSpec:
    """Parsed ``<scheme>:<location>[#<sub-path>]`` hub identifier."""

    scheme: str           # 'local' | 'git' | 'hf'
    location: str         # path / URL / repo_id
    subpath: Optional[str] = None


def parse_spec(spec: str) -> HubSpec:
    """Parse ``local:/...``, ``git:<url>[#<path>]``, ``hf:<repo_id>[#<path>]``."""
    if ':' not in spec:
        raise ValueError(
            f"Hub spec missing scheme prefix: '{spec}'. "
            "Use local:/path, git:<remote>[#path], or hf:<repo_id>[#path]."
        )
    scheme, rest = spec.split(':', 1)
    scheme = scheme.strip()
    if scheme not in ('local', 'git', 'hf'):
        raise ValueError(f"Unknown hub scheme: '{scheme}'")
    if '#' in rest:
        location, subpath = rest.split('#', 1)
    else:
        location, subpath = rest, None
    return HubSpec(scheme=scheme, location=location.strip(), subpath=subpath)


class HubBackend(Protocol):
    """A hub backend stores bundles somewhere and supports push / pull / list."""

    scheme: str

    def push(self, bundle: Bundle, *, message: Optional[str] = None) -> str:
        """Publish a bundle. Returns a backend-specific reference (URL or commit)."""
        ...

    def pull(self, bundle_id: str, *, into: Path) -> Path:
        """Fetch a bundle by id; return the path it was unpacked to."""
        ...

    def list_bundles(self, *, limit: int = 100) -> List[str]:
        """Return bundle IDs available at this hub."""
        ...


__all__ = ['HubBackend', 'HubSpec', 'parse_spec']
