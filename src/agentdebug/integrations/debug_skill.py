"""Generate host-specific AgentDebugX debug skill contracts.

The shared contract is deliberately CLI-first: host agents learn when and how
to call ``agentdebug`` instead of copying AgentDebugX diagnosis logic into
their own prompts. Platform builders only adapt packaging details such as
frontmatter, install paths, and allowed tools.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, Optional

SkillPlatform = Literal['claude', 'hermes', 'openclaw']


def _read_skill_file(rel_path: str) -> str:
    root = files('agentdebug.integrations') / 'agentdebug_skill'
    return (root / rel_path).read_text(encoding='utf-8')


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith('---\n'):
        return markdown.strip()
    _, _, rest = markdown.partition('\n---\n')
    return rest.strip()


CANONICAL_SKILL_MD = _read_skill_file('SKILL.md')
CANONICAL_SKILL_BODY = _strip_frontmatter(CANONICAL_SKILL_MD)

# Compact, reference-free contract for hosts that embed instructions in one file.
SHARED_CONTRACT = (
    '## When To Use' + CANONICAL_SKILL_BODY.split('## When To Use', 1)[1]
).replace(
    ' If `agentdebug` is\n   missing or LLM setup fails, read `references/setup.md`.',
    ' If `agentdebug` is missing, ask before installing `agentdebugx`. If LLM setup fails, explain that `--mode judge`, `--mode deep`, and LLM-backed recovery need `--base-url`/`--api-key`, `AGENTDEBUG_LLM_*`, or `agentdebug config set-llm`.',
)

SETUP_REFERENCE = _read_skill_file('references/setup.md')
CLI_REFERENCE = _read_skill_file('references/cli_reference.md')
FORMAT_REFERENCE = _read_skill_file('references/formats.md')
ANALYSIS_REFERENCE = _read_skill_file('references/analysis.md')
RECOVERY_REFERENCE = _read_skill_file('references/recovery.md')
SAFETY_REFERENCE = _read_skill_file('references/safety.md')


@dataclass
class DebugSkillBundle:
    """In-memory representation of a generated host skill bundle."""

    platform: SkillPlatform
    name: str
    files: dict[str, str]


def build_debug_skill_bundle(
    *,
    platform: SkillPlatform,
    name: str = 'agentdebug',
    extra_files: Optional[Mapping[str, str]] = None,
) -> DebugSkillBundle:
    """Build a host-specific AgentDebugX debug skill bundle."""
    files = _platform_files(platform, name)
    if extra_files:
        files.update(dict(extra_files))
    return DebugSkillBundle(platform=platform, name=name, files=files)


def write_debug_skill_bundle(bundle: DebugSkillBundle, *, target_dir: Path) -> Path:
    """Materialize ``bundle`` below ``target_dir`` and return the root path."""
    target = target_dir.expanduser()
    if bundle.platform == 'claude':
        root = target / bundle.name
    elif bundle.platform == 'hermes':
        root = target / bundle.name
    elif bundle.platform == 'openclaw':
        root = target / bundle.name
    else:  # pragma: no cover - Literal keeps this unreachable.
        raise ValueError(f'unsupported platform: {bundle.platform}')
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in bundle.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return root


def _platform_files(platform: SkillPlatform, name: str) -> dict[str, str]:
    if platform == 'claude':
        return {
            'SKILL.md': _claude_skill_md(name),
            'references/cli_reference.md': CLI_REFERENCE,
            'references/setup.md': SETUP_REFERENCE,
            'references/formats.md': FORMAT_REFERENCE,
            'references/analysis.md': ANALYSIS_REFERENCE,
            'references/recovery.md': RECOVERY_REFERENCE,
            'references/safety.md': SAFETY_REFERENCE,
        }
    if platform == 'hermes':
        return {
            'SKILL.md': _hermes_skill_md(name),
            'references/cli_reference.md': CLI_REFERENCE,
            'references/setup.md': SETUP_REFERENCE,
            'references/formats.md': FORMAT_REFERENCE,
            'references/analysis.md': ANALYSIS_REFERENCE,
            'references/recovery.md': RECOVERY_REFERENCE,
            'references/safety.md': SAFETY_REFERENCE,
        }
    if platform == 'openclaw':
        return {
            'SKILL.md': _openclaw_skill_md(name),
            'references/cli_reference.md': CLI_REFERENCE,
            'references/setup.md': SETUP_REFERENCE,
            'references/formats.md': FORMAT_REFERENCE,
            'references/analysis.md': ANALYSIS_REFERENCE,
            'references/recovery.md': RECOVERY_REFERENCE,
            'references/safety.md': SAFETY_REFERENCE,
        }
    raise ValueError(f'unsupported platform: {platform}')


def _claude_skill_md(name: str) -> str:
    return f"""\
---
name: "{name}"
description: "Debug failed or unclear LLM agent trajectories with AgentDebugX. Use for root-cause analysis, trajectory diagnosis, tool failures, repeated loops, or cross-agent debugging."
argument-hint: "debug this agent run, why did this agent fail, diagnose this trajectory, debug this Hermes/OpenClaw/OpenHands trace"
allowed-tools: Bash(agentdebug *)
---

{CANONICAL_SKILL_BODY}
"""


def _hermes_skill_md(name: str) -> str:
    return f"""\
---
name: {name}
description: Debug failed or unclear LLM agent trajectories with AgentDebugX.
version: 1.0.0
metadata:
  hermes:
    category: debugging
    tags: [agent-debugging, trajectory, root-cause]
    requires_toolsets: [terminal]
---

{CANONICAL_SKILL_BODY}
"""


def _openclaw_skill_md(name: str) -> str:
    return f"""\
---
name: {name}
description: Debug failed or unclear LLM agent trajectories with AgentDebugX.
version: 1.0.0
metadata:
  openclaw:
    category: debugging
    tags: [agent-debugging, trajectory, root-cause]
    requires_tools: [exec, read]
---

{CANONICAL_SKILL_BODY}
"""


__all__ = [
    'CLI_REFERENCE',
    'ANALYSIS_REFERENCE',
    'CANONICAL_SKILL_MD',
    'DebugSkillBundle',
    'FORMAT_REFERENCE',
    'RECOVERY_REFERENCE',
    'SAFETY_REFERENCE',
    'SHARED_CONTRACT',
    'SETUP_REFERENCE',
    'SkillPlatform',
    'build_debug_skill_bundle',
    'write_debug_skill_bundle',
]
