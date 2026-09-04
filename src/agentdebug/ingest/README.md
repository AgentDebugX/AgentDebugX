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


## Native tool-calling consistency (`agentdebug.ingest.native_protocol`)

A transcript recorded from a provider's native tool-calling API carries two descriptions of
every tool turn: the wire envelope (`messages` with `tool_calls` / `tool_call_id`) and the
executed events (`agent.step` or `error` for the turn, `tool.call` / `tool.result` for what
ran). They are written by different code paths and drift silently while every field stays
schema-valid. `native_tool_protocol_violations(trajectory, trace_uid=...)` returns one record
per disagreement (reason, message index, event ids) so a corpus audit can count by reason;
`native_tool_message_violations(messages)` is the subset a strict provider would itself reject
and needs no events, so an importer can run it on the raw export:

```python
from agentdebug.ingest import convert_payload
traj = convert_payload(payload, format="messages", strict_native=True)  # raises ConversionError
```

Conventions the checker reads from a trajectory (all optional; without them it reports
`NO_MESSAGES`): `trajectory.metadata["messages"]`, `event.metadata["prompt_n_messages"]` on the
turn event, `event.metadata["tool_call_id"]` on `tool.call`, `event.input == {"tool", "args"}`,
an `error` starting with `multiple_tool_calls:` for a refused multi-call turn, and
`metadata["text_protocol_fallback"]` on a step that fell back to the text protocol.
