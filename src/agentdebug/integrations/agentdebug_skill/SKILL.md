---
name: agentdebug
description: Use AgentDebugX for trajectory diagnosis only when the user explicitly asks to use AgentDebug, AgentDebugX, or the agentdebug skill. Do not invoke for generic debugging, diagnosis, inspection, or trajectory-review requests.
---

# AgentDebugX Debug Skill

Use the locally installed `agentdebug` CLI to create durable debug runs for
the current captured host session or an explicitly supplied trajectory.

Read references as needed:

- `references/setup.md` before first use, when `agentdebug` is missing, or
  when LLM-backed diagnosis fails due to missing credentials.
- `references/cli_reference.md` for exact `agentdebug` command forms,
  output files, exit codes, LLM configuration, and store usage.
- `references/formats.md` before converting a raw host export or when the
  input format is unclear.
- `references/analysis.md` after diagnosis when interpreting evidence,
  final-answer failures, or confidence.
- `references/recovery.md` when the user wants next-run guidance, repair
  suggestions, verifier ideas, or recovery planning.

## When To Use

Use this skill only when the user explicitly asks to use AgentDebug,
AgentDebugX, or the `$agentdebug` skill. A generic request to debug, diagnose,
inspect, explain, or review a trajectory is not sufficient. Without an
explicit AgentDebug request, handle the task normally without loading or
running this skill.

## Inputs

Accept any of:

- The current Claude Code or Codex session when project auto-capture is enabled
- AgentDebugX-normalized trajectory files
- Raw trajectory exports supported by the installed AgentDebugX adapters
- Benchmark or agent-runtime trajectory directories supported by an adapter
- Independent trajectory collections for batch processing
- Trace IDs from AgentDebugX JSONL or SQLite stores, with the matching store
  option

Do not maintain or infer a fixed framework allowlist here. Adapter support
evolves independently across agent runtimes and benchmark formats. Read
`references/formats.md` when format selection is unclear, and prefer automatic
detection unless the user supplies an explicit format.

Treat target and intent independently. A request to inspect this agent's own,
current, latest, or just-completed work selects the current captured session.
An explicit path or trace ID selects that external target. For an ambiguous
past session, present candidates and ask the user to confirm one; never choose
the most recently modified trace.

## Choose The Operation

First determine whether the supplied input represents one trajectory or an
independent collection. If that cannot be determined from the path and the
user's wording, ask before using batch mode.

- Current/self session:
  `agentdebug run --current --profile quick --json`
- One trajectory or one stored trace ID:
  `agentdebug run <input> --profile standard --json`
- One selected AgentErrorBench record:
  `agentdebug run <input.jsonl> --trajectory-id <id> --profile standard --json`
- Independent JSON files or independent JSONL records:
  `agentdebug run <input> --batch --profile standard --json`
- One OSWorld trajectory directory:
  `agentdebug run <directory> --profile gui --format osworld --json`

A JSONL event stream describing one trajectory is a single input, not a batch.
Directory batch mode recursively discovers JSON files. A directly supplied
JSONL batch treats every non-empty row as one independent trajectory.

GUI RCA collections are outside unified batch mode. Do not route an OSWorld
collection through `agentdebug run --batch`; the separate
`python -m agentdebug.gui` workflow owns its classification, failure filtering,
parallel workers, memory, and output layout.

## Run The Diagnosis

1. Resolve a clearly requested self-debug operation with `--current`. The CLI
   must receive exact session-scoped capture context; do not inspect the store
   for a "latest" trace. If the context is unavailable, explain that current
   capture is not active and ask whether the user wants setup help or has an
   explicit trajectory.
2. Use `quick` for current-session reflection and `standard` for supplied
   trajectories unless the user requests another profile. Use `gui` only for
   one compatible CUA/GUI trajectory. Disclose that `deep` and `gui` are
   LLM-backed before invoking them.
3. Invoke exactly one primary `agentdebug run` operation. Pass through explicit
   `--format`, `--diagnoser`, `--attributor`, or `--recovery` choices instead
   of reinterpreting them.
4. Add `--ui` for a single run only when the user asks for visual inspection or
   an interactive UI. Do not add `--ui` to a batch. After a batch, the user may
   select one returned `run_id` for `agentdebug ui ensure --run-id <run-id>`.
5. Follow the user's objective after diagnosis. Inspection alone calls for a
   report; a request to self-critique and retry may use the diagnosis to form a
   better next attempt; a request to repair a custom agent may authorize code,
   prompt, skill, or tool changes within the stated scope.

## Read The Result

For a single run, read the top-level `status`, `run_id`, `trace_id`,
`report_id`, `trajectory_snapshot_path`, `resolved_pipeline`,
`candidate_root_cause`, `top_evidence`, `ui_url`, `warnings`, and `errors`.

Report a single result in this compact shape, then interpret it for the user's
objective:

```text
Status: <status>
Run: <run_id>
Trace: <trace_id>
Report: <report_id>
Analyzed snapshot: <trajectory_snapshot_path>
Candidate root cause: <summary or unavailable>
Evidence: <top evidence or unavailable>
UI: <ui_url, omitted when absent>
Warnings/errors: <only when present>
```

For a batch, read top-level `status`, `total`, `succeeded`, and `failed`, then
read each `items[]` entry. Successful entries place identities under
`item.result`; record-level failures may have no result and instead expose
`item.errors`.

Report a batch in this compact shape:

```text
Batch status: <status> (<succeeded>/<total> succeeded)
Failed: <failed>
Items:
- <record_id>: <status> | run=<run_id> trace=<trace_id> report=<report_id>
Failures: <record_id and error message, only when present>
```

Do not dump the full report JSON unless the user asks. Preserve the distinction
between trajectory facts, deterministic findings, LLM conclusions, recovery
proposals, and externally supplied labels.

For a current-session target, frame the diagnosis as self-reflection: identify
what this agent should change in its reasoning, verification, or next attempt.
Do not assume an AgentDebugX or host-framework code defect. For an external or
custom-agent target, connect findings to the relevant agent code, prompt,
skill, tool, or runtime only when the evidence supports that connection.

## Ground Rules

- Do not reproduce ingest/diagnose/recovery orchestration in shell commands;
  `agentdebug run` owns that state machine.
- Do not invoke `ingest` or `diagnose` after a successful `run`; that would
  duplicate normalization or diagnosis.
- Do not infer `--batch` merely from a `.jsonl` suffix. Confirm that its rows
  are independent trajectories.
- Do not use unified batch mode for GUI RCA collections.
- Do not automatically open or start one UI per batch item.
- Do not treat a diagnosis-only request as authorization to retry, patch, or
  mutate. When the user explicitly requests diagnosis plus follow-through,
  carry out the authorized retry or repair instead of imposing a report-only
  workflow.
- Recovery output is a proposal. Treat it as next-run guidance unless the user
  separately asks to implement or apply a fix.
- Always say "candidate root cause" or "likely" rather than claiming ground
  truth. Heuristic and DeepDebug reports intentionally omit confidence.
