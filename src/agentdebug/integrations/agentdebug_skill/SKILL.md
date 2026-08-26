---
name: agentdebug
description: Debug failed or unclear LLM agent trajectories with AgentDebugX. Use for root-cause analysis, trajectory diagnosis, tool failures, repeated loops, wrong final answers, or cross-agent debugging.
---

# AgentDebugX Debug Skill

Use the locally installed `agentdebug` CLI to create durable debug runs for
trajectories supplied by the user or an external harness.

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

Use AgentDebugX when the user asks to debug, diagnose, inspect, explain, or
find the root cause of an LLM agent trajectory or failed agent run.

Common triggers:

- failed agent run
- unclear or wrong final answer
- tool failure, timeout, missing file, permission denial, or bad tool args
- repeated loop or repeated failed tool call
- self-debugging or cross-agent debugging

## Inputs

Accept any of:

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

If the user has not provided a trajectory/export path, ask for one. Do not
silently inspect host-local private state to find traces.

## Choose The Operation

First determine whether the supplied input represents one trajectory or an
independent collection. If that cannot be determined from the path and the
user's wording, ask before using batch mode.

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

1. If no trajectory, export, store trace ID, or supported collection was
   supplied, ask the user for one. Never discover or snapshot the current host
   conversation.
2. Use `standard` unless the user explicitly requests another profile. Use
   `gui` only for one compatible CUA/GUI trajectory. Disclose that `deep` and
   `gui` are LLM-backed before invoking them.
3. Invoke exactly one primary `agentdebug run` operation. Pass through explicit
   `--format`, `--diagnoser`, `--attributor`, or `--recovery` choices instead
   of reinterpreting them.
4. Add `--ui` for a single run only when the user asks for visual inspection or
   an interactive UI. Do not add `--ui` to a batch. After a batch, the user may
   select one returned `run_id` for `agentdebug ui ensure --run-id <run-id>`.
5. Treat recovery output as suggest-only. Do not apply a fix or execute a rerun
   without separate user authorization.

## Read The Result

For a single run, read the top-level `status`, `run_id`, `trace_id`,
`report_id`, `resolved_pipeline`, `candidate_root_cause`, `top_evidence`,
`ui_url`, `warnings`, and `errors`.

Report a single result in this compact shape:

```text
Status: <status>
Run: <run_id>
Trace: <trace_id>
Report: <report_id>
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

## Ground Rules

- Do not reproduce ingest/diagnose/recovery orchestration in shell commands;
  `agentdebug run` owns that state machine.
- Do not invoke `ingest` or `diagnose` after a successful `run`; that would
  duplicate normalization or diagnosis.
- Do not infer `--batch` merely from a `.jsonl` suffix. Confirm that its rows
  are independent trajectories.
- Do not use unified batch mode for GUI RCA collections.
- Do not automatically open or start one UI per batch item.
- Do not make trajectory acquisition the default workflow; assume the user has
  provided an exported trajectory or ask for one.
- Do not apply fixes, rerun tools, or mutate a workspace unless the user
  explicitly approves.
- Recovery output is a proposal. Treat it as next-run guidance unless the user
  separately asks to implement or apply a fix.
- Always say "candidate root cause" or "likely" rather than claiming ground
  truth. Heuristic and DeepDebug reports intentionally omit confidence.
