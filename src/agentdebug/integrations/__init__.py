"""Optional host-runtime integrations.

* :mod:`agentdebug.integrations.openhands` — OpenHands microagent-style
  shim. Run AgentDebugX inside an OpenHands session as a sub-module that
  can be called from the host agent's tool surface.
* :mod:`agentdebug.integrations.claude_skill` — generate a Claude Code
  Skill package (``SKILL.md`` + ``reference.md`` + optional ``scripts/``) that
  wraps the AgentDebugX CLI.

These are all *optional* — importing them never pulls in heavyweight host
runtime dependencies. Each provides a thin contract the host can wire to
the AgentDebugX core via the regular `agentdebug` Python API.
"""

from agentdebug.integrations.claude_skill import (
    CAPABILITIES_TEMPLATE,
    SKILL_TEMPLATE,
    SkillBundle,
    build_skill_bundle,
    write_skill_bundle,
)
from agentdebug.integrations.debug_skill import (
    DebugSkillBundle,
    SkillPlatform,
    build_debug_skill_bundle,
    write_debug_skill_bundle,
)
from agentdebug.integrations.codex_skill import (
    build_codex_skill_bundle,
    write_codex_skill_bundle,
)
from agentdebug.integrations.openhands import (
    OpenHandsBridge,
    OpenHandsMicroagentContract,
)

__all__ = [
    'CAPABILITIES_TEMPLATE',
    'DebugSkillBundle',
    'OpenHandsBridge',
    'OpenHandsMicroagentContract',
    'SKILL_TEMPLATE',
    'SkillBundle',
    'SkillPlatform',
    'build_debug_skill_bundle',
    'build_codex_skill_bundle',
    'build_skill_bundle',
    'write_debug_skill_bundle',
    'write_codex_skill_bundle',
    'write_skill_bundle',
]
