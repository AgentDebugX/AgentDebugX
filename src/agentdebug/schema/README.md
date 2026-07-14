# Schema Contracts

`agentdebug.schema` is the stable, framework-independent data boundary for
AgentDebugX artifacts. Ingest adapters produce these models, Diagnose annotates
them, Rerun transports them, and Inspect renders them.

## Public models

- `AgentTrajectory` contains one agent run and its ordered events.
- `AgentEvent` records model, tool, plan, memory, handoff, error, and lifecycle
  observations.
- `FailureMode` and `FailureFinding` represent taxonomy entries and grounded
  detections.
- `DiagnosticReport` contains findings, attribution, recovery, audit entries,
  and metadata.
- `Artifact` references files or other run outputs without embedding
  framework-specific objects.

The current on-disk contract is documented in
[`docs/TRACE_SCHEMA.md`](../../../docs/TRACE_SCHEMA.md). There is no implicit
schema migration layer; readers validate the models that exist in the current
release.

## Extension rules

- Keep models serializable, portable, and independent of Agent frameworks.
- Put framework translation in `agentdebug.ingest`, not in schema models.
- Treat field removal or semantic changes as compatibility-sensitive.
- Keep legacy `agentdebug.core` imports as shims for public symbols that moved
  here.
