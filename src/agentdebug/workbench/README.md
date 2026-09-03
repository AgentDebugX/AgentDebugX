# Workbench

The Workbench owns `agentdebug run`: one durable, resolvable unit of debugging
work over one supplied trajectory. It resolves a profile into a concrete
pipeline, ingests the input, delegates diagnosis to the Diagnose workflow, and
persists a run manifest that ties the input trace and the resulting report to a
single `run_id`.

The CLI command is a thin adapter. Callers that need the same contract from
Python use `workbench.service.execute_run`, `plan_run`, and
`execute_batch_run` directly.

## Profiles

A profile is the user-facing name for a coherent diagnoser, attributor, and
recovery combination:

| Profile | Diagnoser | Attributor | Recovery | LLM |
| --- | --- | --- | --- | --- |
| `quick` | `heuristic` | `none` | `none` | No |
| `standard` | `heuristic` | `heuristic` | `reflexion` | No |
| `deep` | `deep` | `none` | `deepdebug` | Yes |
| `gui` | `gui-rca` | `none` | `reflexion` | Yes |

`standard` is the CLI default; `deep` is the advocated workflow for session
diagnosis and the one the bundled AgentDebug skill runs.

`--diagnoser`, `--attributor`, `--recovery`, and `--format` override individual
slots and are recorded as `override` rather than `profile` in the resolved
pipeline, so a run always states where each value came from. Overrides may
reduce cost but never smuggle LLM work into a local profile silently: a run
whose resolved pipeline requires an LLM sets `llm_required`, and resolution
rejects combinations that make result ownership ambiguous, such as pairing
`deep` with a separate attributor or a non-DeepDebug recovery.

`--plan` resolves and records the pipeline without ingesting, diagnosing, or
writing a report. It produces a `planned` manifest.

## Run Manifests

Every run writes one `DebugRun` JSON manifest atomically, and updates it in
place as the run progresses. It contains:

- `run_id`, `schema_version`, `status`, and creation/update timestamps.
- `input`: the reference the caller supplied, an optional selecting
  `trajectory_id`, and the detected input format.
- `requested_profile` and `resolved_pipeline`, including each value's source.
- `artifacts`: `trace_id`, `report_id`, and the resolved store type and path.
- `candidate_root_cause` and `top_evidence` for a quick read of the outcome.
- `result`: the complete diagnostic report.
- `provenance`: orchestrator, analyzer, model, report metadata, and the input
  snapshot's trace ID, SHA-256, event count, and last event ID.
- `warnings`, `errors`, and suggested follow-up `actions`.
- `ui_url` when `--ui` started the local console successfully.

Status values are `planned`, `running`, `completed`, `partial`, and `failed`.
`partial` means the input was accepted and stored but diagnosis did not
complete — for example a missing LLM credential or an incompatible pipeline. A
failed run is never promoted back to success. UI startup failure is a warning,
not a status change.

## Input References, Not Copies

A run stores its input trajectory once, in the selected trace store, and then
references it by `trace_id` plus the recorded snapshot hash. It does not write
a `<run-id>.trajectory.json` beside the manifest. Diagnosing the same captured
trace repeatedly therefore adds manifests, not duplicate trajectories, and any
manifest can be checked against the exact bytes it analyzed.

The input reference itself may be a stored trace ID, a trajectory or export
file, a directory, or one selected record of a multi-record JSONL dataset.
`--batch` instead treats each independent record as its own run with its own
identities and isolates per-record failures.

## Session-Scoped Runs

Runs are created under `<run-root>/runs/`. When the input trajectory carries
`capture_host` and `capture_host_session_id` metadata — that is, when it came
from automatic capture — the manifest is moved to
`<run-root>/sessions/<host>/<session-id>/runs/<run-id>.json`.

Keeping a session's diagnoses next to that session's immutable traces means the
complete history of one host session is one directory, and deleting a session
removes its diagnoses with it. Lookups and listings span both locations, so a
`run_id` resolves the same way regardless of where its manifest lives.

## Extension Rules

- Add a profile in `profiles.py` with an explicit validity rule; do not let
  callers assemble unvalidated combinations.
- Keep diagnosis logic in `diagnose/` and host capture logic in `capture/`.
- Extend `DebugRun` additively and bump `schema_version` for a breaking change.
- Persist through `RunRegistry` so writes stay atomic and status transitions
  stay checked.
