"""Error Hub — package, publish, and pull agent failure bundles.

The Hub is AgentDebugX's answer to the question "how do I share a failing
trace with my team — or with the world — without leaking PII?". Every share
goes through a typed :class:`Bundle` (trajectory + report + optional
artifacts + manifest) and a default-on scrubber.

Backends:

* :class:`~agentdebug.hub.backends.LocalHub` — directory on disk; the default,
  always available; useful for air-gapped or CI artifact stores.
* :class:`~agentdebug.hub.backends.GitHub` — any Git remote (GitHub, GitLab,
  Gitea, self-hosted) via the ``git`` CLI; no Python dep beyond stdlib.
* :class:`~agentdebug.hub.backends.HuggingFaceHub` — datasets repo via
  ``huggingface_hub``. Requires the ``[hub-hf]`` extra.

CLI:

    agentdebug hub push   <trace_id>  --to <spec>  --store-sqlite <db>
    agentdebug hub pull   <spec>      --bundle <bundle_id>  --into <dir>
    agentdebug hub list   <spec>

where ``<spec>`` is one of ``local:/path``, ``git:<remote>#<path>``,
``hf:<repo_id>``.

Privacy default: every push runs the scrubber first. Pass ``--no-scrub`` to
opt out (intended for trusted internal hubs only).
"""

from agentdebug.hub.backend_base import HubBackend, parse_spec
from agentdebug.hub.backends import (
    GitHubBackend,
    HuggingFaceBackend,
    LocalHubBackend,
    backend_from_spec,
)
from agentdebug.hub.bundle import (
    Bundle,
    BundleManifest,
    build_manifest,
    pack_bundle,
    unpack_bundle,
)
from agentdebug.hub.scrub import (
    DEFAULT_REDACTIONS,
    ScrubReport,
    Scrubber,
    scrub_trajectory,
)

__all__ = [
    'Bundle',
    'BundleManifest',
    'DEFAULT_REDACTIONS',
    'GitHubBackend',
    'HubBackend',
    'HuggingFaceBackend',
    'LocalHubBackend',
    'ScrubReport',
    'Scrubber',
    'backend_from_spec',
    'build_manifest',
    'pack_bundle',
    'parse_spec',
    'scrub_trajectory',
    'unpack_bundle',
]
