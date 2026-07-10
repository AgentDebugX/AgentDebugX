"""Bundle = the unit of share.

A bundle is a directory that contains exactly one trajectory, an optional
diagnostic report, optional binary artifacts, and a manifest. The directory
layout::

    bundle_<id>/
    ├── manifest.json     # required — typed metadata, see BundleManifest
    ├── trajectory.json   # required — AgentTrajectory JSON
    ├── report.json       # optional — DiagnosticReport JSON
    ├── README.md         # auto-generated human-readable summary
    └── artifacts/        # optional — binaries referenced by Artifact.uri

Bundles are framework-, language-, and storage-agnostic: anything that can
read JSON can consume them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from pydantic import BaseModel, ConfigDict, Field

from agentdebug.core.models import (
    AgentTrajectory,
    DiagnosticReport,
    model_to_json,
    new_id,
    report_from_json,
    trajectory_from_json,
)

# Bumped only on breaking changes to the directory layout / manifest schema.
BUNDLE_SCHEMA_VERSION = '1.0.0'


class BundleManifest(BaseModel):
    """Typed metadata for an Error Hub bundle.

    Designed to be both human-readable (the YAML/JSON file is small enough to
    scan) and machine-queryable (every list/optional field is normalized).
    """

    model_config = ConfigDict()

    schema_version: str = BUNDLE_SCHEMA_VERSION
    bundle_id: str
    created_at: datetime
    trace_id: str
    framework: Optional[str] = None
    goal_summary: Optional[str] = None  # truncated, post-scrub
    failure_families: List[str] = Field(default_factory=list)
    failure_mode_ids: List[str] = Field(default_factory=list)
    root_cause_step_index: Optional[int] = None
    root_cause_agent: Optional[str] = None
    n_events: int = 0
    has_report: bool = False
    has_artifacts: bool = False
    license: str = 'CC-BY-4.0'
    contributor: Optional[str] = None       # opt-in author identifier
    contributor_org: Optional[str] = None   # opt-in organization
    scrubbed: bool = True
    scrubber_version: Optional[str] = None
    notes: Optional[str] = None


class Bundle(BaseModel):
    """In-memory representation of an Error Hub bundle.

    Use :func:`pack_bundle` to materialize on disk and :func:`unpack_bundle`
    to read one back.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    manifest: BundleManifest
    trajectory: AgentTrajectory
    report: Optional[DiagnosticReport] = None
    # Maps relative paths (under artifacts/) to absolute source paths on disk
    # at pack time. After unpack, these point at the unpacked artifact paths.
    artifact_paths: Dict[str, str] = Field(default_factory=dict)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _families_from_report(report: Optional[DiagnosticReport]) -> List[str]:
    if report is None:
        return []
    seen: List[str] = []
    for f in report.findings:
        family = f.failure_mode.family
        if family not in seen:
            seen.append(family)
    return seen


def _mode_ids_from_report(report: Optional[DiagnosticReport]) -> List[str]:
    if report is None:
        return []
    seen: List[str] = []
    for f in report.findings:
        mid = f.failure_mode.mode_id
        if mid not in seen:
            seen.append(mid)
    return seen


def _truncate(text: Optional[str], max_chars: int = 200) -> Optional[str]:
    if text is None:
        return None
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars] + '…'
    return text


def build_manifest(
    trajectory: AgentTrajectory,
    *,
    report: Optional[DiagnosticReport] = None,
    artifact_paths: Optional[Dict[str, str]] = None,
    bundle_id: Optional[str] = None,
    license: str = 'CC-BY-4.0',
    contributor: Optional[str] = None,
    contributor_org: Optional[str] = None,
    scrubbed: bool = True,
    scrubber_version: Optional[str] = None,
    notes: Optional[str] = None,
) -> BundleManifest:
    """Compose a :class:`BundleManifest` from a trajectory + optional report."""
    return BundleManifest(
        bundle_id=bundle_id or new_id('bundle'),
        created_at=_utc_now(),
        trace_id=trajectory.trace_id,
        framework=trajectory.framework,
        goal_summary=_truncate(trajectory.goal),
        failure_families=_families_from_report(report),
        failure_mode_ids=_mode_ids_from_report(report),
        root_cause_step_index=(
            report.root_cause_step_index if report is not None else None
        ),
        root_cause_agent=(
            report.root_cause_agent if report is not None else None
        ),
        n_events=len(trajectory.events),
        has_report=report is not None,
        has_artifacts=bool(artifact_paths),
        license=license,
        contributor=contributor,
        contributor_org=contributor_org,
        scrubbed=scrubbed,
        scrubber_version=scrubber_version,
        notes=notes,
    )


def _render_readme(manifest: BundleManifest, trajectory: AgentTrajectory) -> str:
    lines: List[str] = []
    lines.append(f'# Bundle `{manifest.bundle_id}`')
    lines.append('')
    lines.append(f'- **trace_id**: `{manifest.trace_id}`')
    lines.append(f'- **framework**: `{manifest.framework or "unknown"}`')
    if manifest.goal_summary:
        lines.append(f'- **goal**: {manifest.goal_summary}')
    lines.append(f'- **events**: {manifest.n_events}')
    lines.append(f'- **license**: `{manifest.license}`')
    lines.append(f'- **scrubbed**: `{manifest.scrubbed}`')
    if manifest.failure_mode_ids:
        lines.append(
            '- **detected modes**: '
            + ', '.join(f'`{m}`' for m in manifest.failure_mode_ids)
        )
    if manifest.root_cause_step_index is not None:
        lines.append(
            f'- **root cause**: step `{manifest.root_cause_step_index}` '
            f'(agent `{manifest.root_cause_agent}`)'
        )
    lines.append('')
    lines.append('Open with `agentdebug analyze trajectory.json`.')
    lines.append('')
    return '\n'.join(lines)


def _to_dict(model: BaseModel) -> Dict[str, Any]:
    """Pydantic v1/v2-compatible dict export."""
    dumper = getattr(model, 'model_dump', None)
    if callable(dumper):
        out = dumper(mode='json')
        return cast(Dict[str, Any], out)
    import json as _json

    raw = model.json()
    return cast(Dict[str, Any], _json.loads(raw))


def pack_bundle(bundle: Bundle, out_dir: Path) -> Path:
    """Write a bundle to ``out_dir / bundle_<id>/``. Returns the bundle path.

    Any artifact paths in ``bundle.artifact_paths`` are copied under
    ``artifacts/`` of the bundle directory; the manifest is updated in place.
    """
    import shutil

    bundle_root = out_dir / bundle.manifest.bundle_id
    bundle_root.mkdir(parents=True, exist_ok=True)

    (bundle_root / 'trajectory.json').write_text(
        model_to_json(bundle.trajectory, indent=2),
        encoding='utf-8',
    )
    if bundle.report is not None:
        (bundle_root / 'report.json').write_text(
            model_to_json(bundle.report, indent=2),
            encoding='utf-8',
        )

    relative_artifacts: Dict[str, str] = {}
    if bundle.artifact_paths:
        art_dir = bundle_root / 'artifacts'
        art_dir.mkdir(exist_ok=True)
        for rel, src in bundle.artifact_paths.items():
            src_path = Path(src)
            if not src_path.exists():
                continue
            dst = art_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst)
            relative_artifacts[rel] = str(dst.relative_to(bundle_root))

    bundle.manifest = bundle.manifest.model_copy(
        update={'has_artifacts': bool(relative_artifacts)}
    ) if hasattr(bundle.manifest, 'model_copy') else BundleManifest(
        **{**_to_dict(bundle.manifest), 'has_artifacts': bool(relative_artifacts)}
    )

    import json as _json

    (bundle_root / 'manifest.json').write_text(
        _json.dumps(_to_dict(bundle.manifest), indent=2, default=str),
        encoding='utf-8',
    )
    (bundle_root / 'README.md').write_text(
        _render_readme(bundle.manifest, bundle.trajectory),
        encoding='utf-8',
    )
    return bundle_root


def unpack_bundle(bundle_dir: Path) -> Bundle:
    """Load a bundle directory back into memory."""
    import json as _json

    manifest_raw = _json.loads((bundle_dir / 'manifest.json').read_text('utf-8'))
    if hasattr(BundleManifest, 'model_validate'):
        manifest = BundleManifest.model_validate(manifest_raw)
    else:
        manifest = BundleManifest.parse_obj(manifest_raw)
    trajectory = trajectory_from_json(
        (bundle_dir / 'trajectory.json').read_text('utf-8')
    )
    report: Optional[DiagnosticReport] = None
    report_path = bundle_dir / 'report.json'
    if report_path.exists():
        report = report_from_json(report_path.read_text('utf-8'))

    artifacts: Dict[str, str] = {}
    art_dir = bundle_dir / 'artifacts'
    if art_dir.is_dir():
        for p in art_dir.rglob('*'):
            if p.is_file():
                rel = str(p.relative_to(art_dir))
                artifacts[rel] = str(p)

    return Bundle(
        manifest=manifest,
        trajectory=trajectory,
        report=report,
        artifact_paths=artifacts,
    )


__all__ = [
    'BUNDLE_SCHEMA_VERSION',
    'Bundle',
    'BundleManifest',
    'build_manifest',
    'pack_bundle',
    'unpack_bundle',
]
