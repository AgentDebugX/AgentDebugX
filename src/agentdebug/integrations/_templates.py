"""Compatibility aliases generated from the canonical skill contract.

New integrations must consume :mod:`agentdebug.integrations.debug_skill`.
These names remain only for callers of the pre-redesign Claude generator.
"""

from agentdebug.integrations.debug_skill import CANONICAL_SKILL_BODY, CLI_REFERENCE

SKILL_TEMPLATE = """\
---
name: "{name}"
description: "Debug supplied agent trajectories with one durable AgentDebugX run."
argument-hint: "debug this supplied agent trajectory"
allowed-tools: {allowed_tools}
---

""" + CANONICAL_SKILL_BODY

REFERENCE_TEMPLATE = CLI_REFERENCE

CAPABILITIES_TEMPLATE = """\
# AgentDebugX Skill Capabilities

The skill accepts a user- or harness-supplied trajectory, export path, store
trace ID, or supported trajectory collection. It invokes `agentdebug run` and
returns stable run, trajectory, and report identities plus an optional managed
UI link. It does not discover host conversations, install lifecycle hooks,
apply fixes, or execute reruns automatically.
"""

__all__ = ['CAPABILITIES_TEMPLATE', 'REFERENCE_TEMPLATE', 'SKILL_TEMPLATE']
