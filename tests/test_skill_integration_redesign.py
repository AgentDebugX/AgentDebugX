from __future__ import annotations

import json

import pytest

from agentdebug.integrations.debug_skill import CONTRACT_VERSION, build_debug_skill_bundle
from agentdebug.integrations.codex_skill import build_codex_skill_bundle
from agentdebug.integrations.management import MARKER, install_skill, integration_status


@pytest.mark.parametrize('platform', ['claude', 'codex', 'hermes', 'openclaw'])
def test_all_host_skills_share_unified_run_contract(platform: str) -> None:
    bundle = build_debug_skill_bundle(platform=platform)
    skill = bundle.files['SKILL.md']
    assert 'agentdebug run <input> --profile standard --json' in skill
    assert 'agentdebug run <input> --batch' in skill
    assert '--trajectory-id <id>' in skill
    assert 'item.result' in skill
    assert 'agentdebug diagnose <trajectory.json>' not in skill
    assert CONTRACT_VERSION in skill
    if platform == 'codex':
        assert 'agents/openai.yaml' in bundle.files


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
    assert 'agentdebug run <input> --profile standard --json' in bundle.files['SKILL.md']
    metadata = bundle.files['agents/openai.yaml']
    assert 'display_name: agentdebug' in metadata
    assert 'Use $agentdebug ' in metadata
    assert 'supplied trajectory or collection' in metadata
