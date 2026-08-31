# Automatic Capture

This package converts supported Claude Code and Codex transcript snapshots into
cumulative AgentDebugX trajectories. Capture is opt-in and silent: a project
that has not consented exits before doing any work, and storage contention is
absorbed so the host session continues. It never starts diagnosis or invokes an
LLM.

Fail-open is not yet total. Hook dispatch currently absorbs only
`sqlite3.OperationalError`; a malformed host payload or an unknown event name
propagates as a non-zero exit with a traceback on stderr.

## Flow

1. Validate the host notification and capture configuration.
2. Normalize, filter, and redact complete transcript records.
3. Atomically store the trajectory and capture receipt in project SQLite.
4. Reconcile pending or failed receipts on a later capture event.

Host-specific parsing belongs in `hosts/`; shared identity, filtering,
snapshot, repository, and orchestration logic stays in this package.

## Project Layout

Capture writes everything beneath the consenting project:

```text
.agentdebug/
├── capture.json
├── agentdebug.sqlite
├── capture-context/
├── sessions/
│   ├── claude_code/<session-id>/
│   │   ├── session.json
│   │   ├── traces/0001.json
│   │   └── runs/<run-id>.json
│   └── codex/<session-id>/...
└── runs/<run-id>.json
```

- `capture.json`: project consent — the project root, store path, and the
  per-host enabled flag and captured event list.
- `agentdebug.sqlite`: the canonical queryable store holding trajectories,
  reports, capture receipts, sessions, and the trace sequence.
- `capture-context/`: one pointer per active host session, naming the host,
  session ID, project root, and store that `agentdebug run --current` must use.
- `session.json`: the session index — host, session ID, update time, and the
  ordered trace metadata for that session.
- `traces/NNNN.json`: immutable normalized trajectory snapshots, numbered in
  capture order. An existing file is never rewritten.
- Session `runs/`: diagnoses of that captured session, written by the
  Workbench and moved here once the run's input trajectory is identified as
  belonging to the session.
- Top-level `runs/`: diagnoses of explicitly supplied, non-session inputs such
  as a trajectory file or an imported export.

The readable files mirror what SQLite already holds; the store stays the source
of truth for queries.

## Lazy Session Materialization

Starting a host session creates nothing durable. On `SessionStart`, Claude Code
capture only exports the *future* context path into the session environment.
The first real `UserPromptSubmit` writes the session context file under
`capture-context/`, and the first completed response boundary writes
`traces/0001.json` and the session row.

The result is that launching a host and exiting without prompting leaves no
context file, session, or trace, and `agentdebug run --current` reports that
the session has no captured trace yet rather than selecting an unrelated one.

## Duplicate Suppression

Capture is driven by several host events per turn and must stay idempotent
without diffing trajectories. Suppression is metadata-based and layered:

- A receipt ID derived from the notification and adapter version makes a
  repeated dispatch of the same event a no-op.
- A boundary ID derived from the request and transcript snapshot is unique per
  session, so a second event describing the same logical boundary commits a
  no-op with a `duplicate content boundary` warning.
- An unchanged transcript hash against the stored session commits a no-op
  before normalization.
- A boundary that normalizes to no meaningful events commits a no-op with a
  `no meaningful events` warning.

Only a boundary that survives all four checks appends a new numbered trace, so
resuming a captured session extends it instead of creating a second session
directory.
