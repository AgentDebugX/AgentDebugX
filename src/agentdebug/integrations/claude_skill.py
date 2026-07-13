"""Generate a Claude Code Skill package wrapping AgentDebugX.

Claude Code skills are folders of ``SKILL.md`` + a ``references/`` directory
(and an optional ``scripts/`` directory) that Claude can load on demand. We
ship a generator (``write_skill_bundle``) and a CLI command
(``agentdebug integrations skill``) that materialize an AgentDebugX skill into
the user's ``~/.claude/skills/`` directory (or any target path).

The generated skill calls back into the ``agentdebug`` CLI for execution, so it
stays in sync with whatever version of AgentDebugX the user has installed — the
skill never forks the CLI's analysis logic into Python. Bundled scripts (see
``SKILL_SCRIPTS``) are the one exception: small, stdlib-only helpers for
content checks and normalizing the often-messy raw trace output — not
substitutes for the CLI.

Template strings live in ``_templates.py``:

* ``SKILL_TEMPLATE``        → ``SKILL.md``                     (formatted with ``{name}`` / ``{allowed_tools}``)
* ``REFERENCE_TEMPLATE``    → ``references/cli_reference.md``
* ``CAPABILITIES_TEMPLATE`` → ``references/capabilities.md``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentdebug.integrations.debug_skill import (
    build_debug_skill_bundle,
)
from agentdebug.integrations._templates import (
    CAPABILITIES_TEMPLATE,
    REFERENCE_TEMPLATE,
    SKILL_TEMPLATE,
)

__all__ = [
    'CAPABILITIES_TEMPLATE',
    'REFERENCE_TEMPLATE',
    'SKILL_TEMPLATE',
    'SkillBundle',
    'build_skill_bundle',
    'write_skill_bundle',
]


SKILL_SCRIPTS: dict[str, str] = {}
"""Optional dependency-free helper scripts shipped under ``scripts/``.

Maps a path relative to the skill root (e.g. ``'scripts/normalize_trace.py'``)
to the script's source. These are *not* substitutes for the ``agentdebug``
CLI — they are small, stdlib-only helpers for things the CLI does not do, like
sanity-checking content or normalizing the often-messy raw trace output before
it is handed to ``agentdebug convert``. Empty by default; populate as concrete
needs arise and the generator will materialize them, add ``Bash(python3 *)`` to
the skill's ``allowed-tools``, and list them under "Helper scripts" in
``SKILL.md``.
"""


@dataclass
class SkillBundle:
    """In-memory representation of the generated Skill."""

    name: str
    skill_md: str
    extra_files: dict[str, str]


def _render_skill_md(name: str) -> str:
    """Render ``SKILL.md``, wiring in helper scripts when any are registered."""
    skill_md = build_debug_skill_bundle(platform='claude', name=name).files['SKILL.md']
    if SKILL_SCRIPTS:
        skill_md = skill_md.replace(
            'allowed-tools: Bash(agentdebug *)',
            'allowed-tools: Bash(agentdebug *) Bash(python3 *)',
        )
    if not SKILL_SCRIPTS:
        return skill_md
    listing = '\n'.join(f'* `{path}`' for path in sorted(SKILL_SCRIPTS))
    # Note: ${CLAUDE_SKILL_DIR} is expanded by Claude Code at run time, and is
    # intentionally assembled outside of .format() so its braces survive.
    return skill_md + (
        '\n## Helper scripts\n\n'
        'This skill bundles small, stdlib-only helper scripts. They assist with '
        'content checks and normalizing messy trace output. Run one with:\n\n'
        '```bash\n'
        'python3 ${CLAUDE_SKILL_DIR}/scripts/<name>.py\n'
        '```\n\n'
        'Available scripts:\n\n'
        f'{listing}\n'
    )


def build_skill_bundle(*, name: str = 'agentdebug') -> SkillBundle:
    """Build a SkillBundle for ``name``. ``write_skill_bundle`` materializes it."""
    shared = build_debug_skill_bundle(platform='claude', name=name)
    return SkillBundle(
        name=name,
        skill_md=_render_skill_md(name),
        extra_files={
            'references/cli_reference.md': shared.files['references/cli_reference.md'],
            'references/setup.md': shared.files['references/setup.md'],
            'references/formats.md': shared.files['references/formats.md'],
            'references/analysis.md': shared.files['references/analysis.md'],
            'references/recovery.md': shared.files['references/recovery.md'],
            'references/safety.md': shared.files['references/safety.md'],
            'references/capabilities.md': CAPABILITIES_TEMPLATE,
            **SKILL_SCRIPTS,
        },
    )


def write_skill_bundle(bundle: SkillBundle, *, target_dir: Path) -> Path:
    """Materialize the Skill at ``target_dir / <name>/``."""
    skill_root = target_dir.expanduser() / bundle.name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / 'SKILL.md').write_text(bundle.skill_md, encoding='utf-8')
    for rel, content in bundle.extra_files.items():
        path = skill_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return skill_root
