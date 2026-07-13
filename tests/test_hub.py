from __future__ import annotations

import re

import pytest

from agentdebug.hub import Bundle, build_manifest, pack_bundle, scrub_trajectory, unpack_bundle
from agentdebug.schema import AgentEvent, AgentTrajectory, DiagnosticReport, EventType


def test_scrubber_redacts_nested_secrets_and_pii() -> None:
    trajectory = AgentTrajectory(
        trace_id='sensitive',
        goal='Email owner@example.com with sk-abcdefghijklmnopqrstuvwxyz123456',
        metadata={'phone': '+14155552671'},
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trajectory.trace_id,
            event_type=EventType.TOOL_CALL,
            input={'authorization': 'Bearer abcdefghijklmnopqrstuvwxyz1234'},
            output=['owner@example.com'],
            error='SSN 123-45-6789',
        )
    )

    report = scrub_trajectory(trajectory)

    rendered = str(trajectory)
    assert 'owner@example.com' not in rendered
    assert 'sk-abcdefghijklmnopqrstuvwxyz123456' not in rendered
    assert '+14155552671' not in rendered
    assert '123-45-6789' not in rendered
    assert report.total() >= 5


def test_scrubber_supports_custom_redaction() -> None:
    trajectory = AgentTrajectory(trace_id='custom', goal='internal-project-42')

    report = scrub_trajectory(
        trajectory,
        extra_redactions=[
            ('project', re.compile(r'internal-project-\d+'), '<redacted:project>')
        ],
    )

    assert trajectory.goal == '<redacted:project>'
    assert report.replacements == {'project': 1}


def test_bundle_pack_unpack_round_trip(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    artifact = tmp_path / 'evidence.txt'
    artifact.write_text('evidence', encoding='utf-8')
    manifest = build_manifest(
        failed_trajectory,
        report=diagnostic_report,
        artifact_paths={'logs/evidence.txt': str(artifact)},
        bundle_id='bundle-test',
        scrubbed=True,
        scrubber_version='test',
    )
    bundle = Bundle(
        manifest=manifest,
        trajectory=failed_trajectory,
        report=diagnostic_report,
        artifact_paths={'logs/evidence.txt': str(artifact)},
    )

    bundle_path = pack_bundle(bundle, tmp_path / 'hub')
    restored = unpack_bundle(bundle_path)

    assert restored.manifest.bundle_id == 'bundle-test'
    assert restored.trajectory == failed_trajectory
    assert restored.report == diagnostic_report
    assert 'logs/evidence.txt' in restored.artifact_paths
    assert (bundle_path / 'README.md').exists()


def test_bundle_skips_missing_artifacts(
    tmp_path,
    failed_trajectory: AgentTrajectory,
) -> None:
    manifest = build_manifest(
        failed_trajectory,
        artifact_paths={'missing.txt': str(tmp_path / 'missing.txt')},
        bundle_id='bundle-missing',
    )
    bundle = Bundle(
        manifest=manifest,
        trajectory=failed_trajectory,
        artifact_paths={'missing.txt': str(tmp_path / 'missing.txt')},
    )

    restored = unpack_bundle(pack_bundle(bundle, tmp_path / 'hub'))

    assert restored.manifest.has_artifacts is False
    assert restored.artifact_paths == {}


def test_bundle_rejects_artifact_path_traversal(
    tmp_path,
    failed_trajectory: AgentTrajectory,
) -> None:
    artifact = tmp_path / 'secret.txt'
    artifact.write_text('secret', encoding='utf-8')
    manifest = build_manifest(
        failed_trajectory,
        artifact_paths={'../../escaped.txt': str(artifact)},
        bundle_id='bundle-unsafe',
    )
    bundle = Bundle(
        manifest=manifest,
        trajectory=failed_trajectory,
        artifact_paths={'../../escaped.txt': str(artifact)},
    )

    with pytest.raises(ValueError, match='artifact path'):
        pack_bundle(bundle, tmp_path / 'hub')


def test_unpack_rejects_missing_manifest(tmp_path) -> None:
    bundle_dir = tmp_path / 'broken'
    bundle_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        unpack_bundle(bundle_dir)
