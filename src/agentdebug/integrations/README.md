# Integrations Workflow

Integrations generate or adapt assets that let external agent tools use
AgentDebugX diagnostics.

## When to use

Use Integrations when AgentDebugX needs to interoperate with another developer
tool, agent runtime, or assistant environment.

Current integration families include:

- managed Claude Code and Codex skills
- opt-in Claude Code and Codex automatic-capture hooks
- generated Hermes and OpenClaw skills using the same canonical run contract
- OpenHands integration assets
- shared reference documents and generic debug skill templates

## Flow

1. Select the target integration.
2. Render the canonical `agentdebug run` contract with host-edge metadata.
3. Install or generate host assets, or enable project capture consent.
4. Validate ownership, contract version, CLI availability, UI extras, and the
   writable run root with `agentdebug integrations status`.

## Dependencies

Most generation paths are local. The generated assets may assume that
AgentDebugX is installed and that the target tool can invoke the CLI or read the
documented formats.

## Extension Rules

- Keep reusable templates in this package.
- Keep generated assets documented and self-contained.
- Follow the accepted [native plugin installation decision](../../../AUTO_CAPTURE_PLUGIN_INSTALLATION.md)
  for automatic capture distribution and project activation.
- Do not mix integration generation with Diagnose implementation.
- Preserve legacy `diagnose/actions/integrations` imports as compatibility
  shims.
