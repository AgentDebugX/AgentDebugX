from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdebug.integrations.debug_skill import CONTRACT_VERSION, build_debug_skill_bundle
from agentdebug.integrations.codex_skill import build_codex_skill_bundle
from agentdebug.integrations.management import MARKER, install_skill, integration_status


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('platform', ['claude', 'codex', 'hermes', 'openclaw'])
def test_all_host_skills_share_unified_run_contract(platform: str) -> None:
    bundle = build_debug_skill_bundle(platform=platform)
    skill = bundle.files['SKILL.md']
    assert 'agentdebug run <input> --profile deep --json' in skill
    assert 'agentdebug run --current --profile deep --json' in skill
    assert 'DeepDebug' in skill
    assert 'references/cli_reference.md' in skill
    assert 'agentdebug diagnose <trajectory.json>' not in skill
    assert 'only when the user explicitly asks' in skill
    assert 'generic debugging requests normally' in skill
    if platform == 'codex':
        assert 'agents/openai.yaml' in bundle.files
        assert 'allow_implicit_invocation: false' in bundle.files['agents/openai.yaml']


def test_managed_install_refuses_unmanaged_and_preserves_unrelated_files(tmp_path) -> None:
    root = tmp_path / 'skills' / 'agentdebug'
    root.mkdir(parents=True)
    (root / 'mine.txt').write_text('keep', encoding='utf-8')
    with pytest.raises(ValueError, match='unmanaged'):
        install_skill('claude', target=str(tmp_path / 'skills'))
    install_skill('claude', target=str(tmp_path / 'skills'), force=True)
    assert (root / 'mine.txt').read_text(encoding='utf-8') == 'keep'
    marker = json.loads((root / MARKER).read_text(encoding='utf-8'))
    assert marker['contract_version'] == CONTRACT_VERSION
    install_skill('claude', target=str(tmp_path / 'skills'))
    assert integration_status('claude', target=str(tmp_path / 'skills'), run_root=str(tmp_path))['ready'] is True


def test_codex_packaging_edge_uses_canonical_bundle() -> None:
    bundle = build_codex_skill_bundle()
    assert bundle.platform == 'codex'
    assert 'agentdebug run <input> --profile deep --json' in bundle.files['SKILL.md']
    metadata = bundle.files['agents/openai.yaml']
    assert 'display_name: "agentdebug"' in metadata
    assert 'Use $agentdebug ' in metadata
    assert 'this captured session or a supplied trajectory' in metadata


@pytest.mark.parametrize(
    'platform,target',
    [
        ('claude', 'integrations/claude-code/plugins/agentdebug/skills/agentdebug'),
        ('codex', 'integrations/codex/plugins/agentdebug/skills/agentdebug'),
    ],
)
def test_native_plugin_skill_matches_canonical_bundle(
    platform: str, target: str
) -> None:
    bundle = build_debug_skill_bundle(platform=platform)
    root = ROOT / target
    actual = {
        str(path.relative_to(root))
        for path in root.rglob('*')
        if path.is_file()
    }
    assert actual == set(bundle.files)
    for relative, content in bundle.files.items():
        assert (root / relative).read_text(encoding='utf-8') == content
    assert 'trajectory_snapshot_path' not in bundle.files['SKILL.md']
