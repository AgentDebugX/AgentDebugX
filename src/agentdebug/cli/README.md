# AgentDebug CLI

The CLI is the user-facing workflow entrypoint for AgentDebugX. It should stay
thin: commands parse arguments, dispatch to workflow modules, and preserve
backward-compatible command names.

## When to use

Use the CLI when a user needs to run AgentDebugX from a terminal, script,
benchmark harness, or CI job.

Primary commands:

- `agentdebug diagnose` runs the Diagnose workflow.
- `agentdebug ingest` imports traces from external formats.
- `agentdebug rerun` prepares or executes Rerun workflows.
- `agentdebug hub` manages Error Hub bundles.
- `agentdebug integrations` generates integration assets.
- `agentdebug serve` starts the inspection API or UI.
- `agentdebug doctor` checks local configuration.

## Flow

1. `main.py` builds the parser and registers command modules.
2. `commands/*` modules expose workflow-specific `run(...)` handlers.
3. `legacy.py` contains compatibility implementation that has not yet moved
   into dedicated workflow modules.
4. Workflow modules under `diagnose/`, `ingest/`, `rerun/`, `hub/`, and
   `integrations/` perform the real work.

## Dependencies

The base CLI only depends on the core AgentDebugX package. Some commands require
optional extras:

- `serve`: `agentdebugx[ui]`
- `ingest` adapters: integration-specific extras such as `langgraph`, `crewai`,
  `openai-agents`, or `otel`
- `hub` uploads: `agentdebugx[hub-hf]` when using Hugging Face backends

## Extension Rules

- Add a new command by creating `cli/commands/<name>.py` and registering it in
  `main.py`.
- Keep command modules focused on parsing and dispatch.
- Put reusable behavior in the owning workflow package, not in `cli/legacy.py`.
- Preserve old command names and option semantics unless a compatibility shim is
  provided.

