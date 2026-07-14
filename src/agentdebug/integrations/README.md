# Integrations Workflow

Integrations generate or adapt assets that let external agent tools use
AgentDebugX diagnostics.

## When to use

Use Integrations when AgentDebugX needs to interoperate with another developer
tool, agent runtime, or assistant environment.

Current integration families include:

- Claude Code, Hermes, and OpenClaw debugging skill generation
- OpenHands integration assets
- shared reference documents and generic debug skill templates

## Flow

1. Select the target integration.
2. Render templates or package static skill resources.
3. Write generated assets to the requested output location.
4. Let the external tool load those assets through its normal mechanism.

## Dependencies

Most generation paths are local. The generated assets may assume that
AgentDebugX is installed and that the target tool can invoke the CLI or read the
documented formats.

## Extension Rules

- Keep reusable templates in this package.
- Keep generated assets documented and self-contained.
- Do not mix integration generation with Diagnose implementation.
- Preserve legacy `diagnose/actions/integrations` imports as compatibility
  shims.
