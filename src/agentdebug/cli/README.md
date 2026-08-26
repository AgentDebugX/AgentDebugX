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
- `agentdebug batch ingest` imports directories or independent JSONL records.
- `agentdebug batch diagnose` diagnoses a collection with failure isolation.
- `agentdebug rerun` prepares or executes Rerun workflows.
- `agentdebug runner serve` exposes an application-owned Agent callback through
  the persistent live Rerun HTTP protocol.
- `agentdebug hub` manages Error Hub bundles.
- `agentdebug integrations` generates assets and manages opt-in host capture.
- `agentdebug serve` starts the inspection API or UI.
- `agentdebug doctor` checks local configuration.

## Diagnose option contract

For regular diagnosis modes, callers explicitly select the diagnosis engine,
attributor, and recovery strategy with `--mode`, `--attributor`, and
`--recovery`.

DeepDebug is different: `--mode deep` (or `--mode deepdebug`) selects a
complete diagnosis workflow with its own attribution and fix guidance. The CLI
first runs deterministic Detect and injects its findings as fallible prior
signals, then automatically packages DeepDebug's evidence-grounded attribution
and fix guidance as a standard retry directive:

```bash
agentdebug diagnose TRACE \
  --mode deepdebug
```

Use `--recovery deepdebug` to select the same packaging explicitly. Existing
scripts may pass `--recovery none` to disable the standard recovery payload.

## Rerun option contract

Rerun has three explicit modes:

- `--plan-only` writes an auditable request without executing an actor.
- `--plan-only --actor-task-format jsonl|parquet` exports pending rollout inputs
  for a user-owned actor pipeline; it does not create training labels.
- `--simulate` asks an LLM for a labeled hypothetical trajectory and executes no
  tools.
- Live execution is the default when a persistent runner is configured. Select
  one with `--runner NAME`, or use `--runner-command` for process compatibility.

Configure reusable HTTP runners with `agentdebug config set-runner`, inspect
them with `list-runners`, select a default with `use-runner`, and verify them
with `doctor-runner`. These mode flags are intentionally mutually exclusive.

## Flow

1. `main.py` builds the parser and registers command modules.
2. `commands/*` modules expose workflow-specific `run(...)` handlers.
3. `legacy.py` contains compatibility implementation that has not yet moved
   into dedicated workflow modules.
4. Workflow modules under `batch/`, `diagnose/`, `ingest/`, `rerun/`, `hub/`, and
   `integrations/` perform the real work.

## Dependencies

The base CLI only depends on the core AgentDebugX package. Some commands require
optional extras:

- `serve`: `agentdebugx[ui]`
- `runner serve`: `agentdebugx[ui]`
- `ingest` adapters: integration-specific extras such as `langgraph`, `crewai`,
  `openai-agents`, or `otel`
- `hub` uploads: `agentdebugx[hub-hf]` when using Hugging Face backends
- Parquet actor-task export: `pyarrow`

## Extension Rules

- Add a new command by creating `cli/commands/<name>.py` and registering it in
  `main.py`.
- Keep command modules focused on parsing and dispatch.
- Put reusable behavior in the owning workflow package, not in `cli/legacy.py`.
- Preserve old command names and option semantics unless a compatibility shim is
  provided.
