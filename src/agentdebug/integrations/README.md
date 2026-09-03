# Integrations Workflow

Integrations generate or adapt assets that let external agent tools use
AgentDebugX diagnostics.

## When to use

Use Integrations when AgentDebugX needs to interoperate with another developer
tool, agent runtime, or assistant environment.

Current integration families include:

- managed Claude Code and Codex skills
- native Claude Code and Codex plugins that bundle opt-in automatic-capture
  hooks together with the canonical AgentDebug skill
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
- Distribute automatic capture only through the native Claude Code and Codex
  plugins under `integrations/`. Do not install capture by editing
  `.claude/settings.json` or Codex hook configuration.
- Keep plugin installation and project activation separate. Installing a plugin
  never enables capture; `agentdebug integrations capture enable` owns project
  consent, capture state, receipts, and trajectories, while the host owns hook
  discovery, scopes, trust, updates, and uninstall.
- Treat the skill copies bundled in each plugin as generated output of
  `agentdebug_skill/`. Regenerate them with
  `PYTHONPATH=src python scripts/sync_plugin_skills.py` and never edit them
  independently; `--check` fails when a copy is stale.
- Do not mix integration generation with Diagnose implementation.
- Preserve legacy `diagnose/actions/integrations` imports as compatibility
  shims.
