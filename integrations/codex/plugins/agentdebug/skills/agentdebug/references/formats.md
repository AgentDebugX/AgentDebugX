# Host Trace Formats

The main skill workflow starts after the user provides a trajectory or exported
trace artifact. Use host export commands only when the user explicitly asks how
to obtain that artifact.

## Output Naming

When the user does not specify paths, keep generated files under `.agentdebug/`
with meaningful names:

```text
.agentdebug/
  <case>.trajectory.json
  <case>.traceback.txt
  <case>.report.json
```

Use a stable case name from the input filename, trace id, or session title.
Avoid writing normalized outputs next to private source logs unless the user
asks.

## Hermes

Preferred native input is a Hermes CLI session export JSONL. If the user needs
help exporting one, ask before reading private host state and use Hermes CLI:

```bash
hermes sessions export session.jsonl --session-id <session-id>
hermes sessions export telegram-history.jsonl --source telegram
hermes sessions export backup.jsonl
```

Each line is one session object with full session metadata and messages:

```json
{
  "id": "sess-1",
  "source": "telegram",
  "model": "z-ai/glm-5.2",
  "started_at": 1783022476.0,
  "title": "Deploy docs",
  "messages": [
    {"role": "user", "content": "Deploy the docs."},
    {"role": "assistant", "tool_calls": "[...]"},
    {"role": "tool", "tool_call_id": "call_1", "content": "Error: ..."}
  ]
}
```

The older AgentDebugX wrapper shape
`{"hermes_session": {...}, "messages": [...]}` is also accepted.

`agentdebug ingest --format hermes` converts one Hermes session object at a
time. For a JSONL file containing one independent Hermes session per line, use
the batch workflow:

Run:

```bash
agentdebug batch ingest hermes.jsonl \
  --format hermes \
  --out-dir .agentdebug/hermes
```

After conversion, confirm message retention:

```bash
jq '.metadata.hermes_source, .metadata.hermes_message_count, (.events | length)' .agentdebug/hermes.trajectory.json
jq '[.events[].metadata.hermes_message_id] | map(select(. != null)) | unique | length' .agentdebug/hermes.trajectory.json
```

Hermes roles map as follows:

| Hermes row | AgentDebugX event |
|---|---|
| `role=user` | `observation` |
| assistant `reasoning` | `reflection` |
| assistant content | `llm.response` |
| assistant `tool_calls` | `tool.call` |
| `role=tool` | `tool.result` |

`source` is not always `cli`; preserve and report the actual source value.

## OpenClaw

Expected demo input is a session JSONL stream with `session`, `session-meta`,
and `message` records. Tool blocks may use either Anthropic-style
`tool_use` / `tool_result` or Pi-style `toolUse` / `toolResult`.

Run:

```bash
agentdebug ingest session.jsonl --format openclaw --out .agentdebug/openclaw.trajectory.json
```

## OpenHands

Use a provided OpenHands event export once an `openhands_events` importer is
available. Until then, a pre-normalized AgentDebugX trajectory is the stable
recorded path.

## Native AgentDebugX

If the input already has top-level `trace_id` and `events`, it is a native
`AgentTrajectory`:

```bash
agentdebug ingest trajectory.json --format agenttrajectory --out .agentdebug/native.trajectory.json
```

For a native trajectory, conversion is mostly validation plus optional metadata
overrides.
