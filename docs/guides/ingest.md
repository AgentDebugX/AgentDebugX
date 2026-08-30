# Ingest traces

Use Ingest when the run was produced outside AgentDebugX or is still in a framework-specific export format.

## Convert one file

Auto-detect a JSON or JSONL export:

```bash
agentdebug ingest raw_trace.json \
  --format auto \
  --out trace.json
```

When detection is ambiguous, select a format explicitly:

```bash
agentdebug ingest messages.json \
  --format messages \
  --task-id checkout-42 \
  --goal "Complete checkout" \
  --framework my-agent \
  --out trace.json
```

## Supported CLI format names

The current CLI accepts:

```text
agenttrajectory
messages
message_list
conversations
event_list
webshop_pages
openai_agents_spans
crewai_events
langgraph_callbacks
openclaw
claude_code
hermes
osworld
```

`auto` asks the importer to infer a supported format from the payload.

Framework integrations may require the corresponding optional extra. See [Installation](../getting-started/installation.md).

## Process a JSONL collection

Each non-empty line is treated as an independent record:

```bash
agentdebug batch ingest dataset.jsonl \
  --format auto \
  --out-dir normalized
```

Batch diagnosis performs normalization and diagnosis in one command:

```bash
agentdebug batch diagnose dataset.jsonl \
  --format auto \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out-dir runs
```

Every batch writes `batch-summary.json`. Invalid inputs are isolated from successful records. A partially failed CLI batch exits with status `3`.

## Import an OSWorld trajectory directory

OSWorld input is a directory containing its trajectory JSONL, result metadata, and screenshots:

```bash
agentdebug ingest path/to/osworld-task \
  --format osworld \
  --out osworld-trace.json
```

The adapter records the resolved source directory in trajectory metadata and attaches screenshot paths as image artifacts. GUI RCA uses that on-disk source directory to inspect the original evidence.

## Validate the normalized output

A converted trajectory should contain:

- a stable `trace_id`,
- optional task, goal, and framework metadata,
- an ordered `events` list,
- event types from the canonical enum,
- source-specific details under `metadata`, and
- artifact URI references for files or screenshots.

See the [Trace schema](../TRACE_SCHEMA.md) for the full contract.

!!! warning "Keep source files available for GUI RCA"

    OSWorld ingest stores screenshot URI references; it does not embed all pixels in the normalized JSON. Moving or deleting the source trajectory directory can make later screenshot inspection unavailable.
