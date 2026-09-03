# Ingest Workflow

Ingest converts traces from external agent frameworks and benchmark runs into
AgentDebugX portable trajectory artifacts.

## When to use

Use Ingest when the source data was produced outside AgentDebugX or needs to be
normalized before Diagnose.

Supported adapter families include:

- raw AgentDebugX-compatible JSON
- LangGraph
- CrewAI
- OpenAI Agents SDK
- OpenTelemetry
- Claude Code session JSONL
- Hermes session exports
- OpenClaw session and trajectory JSONL
- GAIA/Open Deep Research style runs
- OSWorld and GUI-oriented traces
- TrajDebug / TRAJERRBENCH unified trajectory JSON

## Flow

1. Select an adapter from `ingest/adapters/`.
2. Parse source files or framework events.
3. Normalize data into `agentdebug.schema` models.
4. Persist traces through `agentdebug.runtime.storage` when requested.
5. Pass normalized artifacts to Diagnose or downstream tooling.

## Dependencies

Raw JSON ingest is dependency-light. Framework-specific adapters may require
their matching optional extras.

## Extension Rules

- Add framework-specific parsing under `ingest/adapters/`.
- Keep normalized outputs aligned with `agentdebug.schema`.
- Avoid leaking framework-specific objects into Diagnose.
- Make adapter failures explicit and actionable.
