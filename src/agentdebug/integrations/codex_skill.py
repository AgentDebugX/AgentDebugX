"""Codex packaging edge for the canonical AgentDebugX skill contract."""

from __future__ import annotations

from pathlib import Path

from .debug_skill import DebugSkillBundle, build_debug_skill_bundle, write_debug_skill_bundle


def build_codex_skill_bundle(*, name: str = 'agentdebug') -> DebugSkillBundle:
    return build_debug_skill_bundle(platform='codex', name=name)


def write_codex_skill_bundle(
    bundle: DebugSkillBundle, *, target_dir: Path
) -> Path:
    if bundle.platform != 'codex':
        raise ValueError('expected a Codex skill bundle')
    return write_debug_skill_bundle(bundle, target_dir=target_dir)


__all__ = ['build_codex_skill_bundle', 'write_codex_skill_bundle']
