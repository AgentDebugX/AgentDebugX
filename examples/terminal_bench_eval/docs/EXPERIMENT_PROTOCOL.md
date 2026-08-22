# Terminal-Bench 2.1 Recovery Experiment Protocol

Status: proposed protocol, revised 2026-08-16. The pinned Claude installer,
legacy fresh-retry harness, and seed/rerun/rerun-deep resume orchestration
(`resume_experiment.py`) are implemented, offline-tested, and have completed
a real end-to-end run against Harbor (seed, rerun, and rerun-deep all
completed for `raman-fitting`, 2026-08-16 — reward `0.0` on all three; one
trial only, no conclusion should be drawn from it). rerun-adamast and
rerun-skill are not implemented.

## Research question

Does an AgentDebugX diagnosis help Claude Code recover from a
verifier-confirmed Terminal-Bench failure beyond the improvement obtained from
one additional attempt with the same conversation context?

This is a practical recovery-headroom study. It does not isolate every causal
effect of reflection, additional inference, or tool use.

## Implementation status

| Capability | Status |
|---|---|
| Configurable pinned Claude Code installer | Implemented and validated |
| Cached-SIF install-only matrix | Implemented and validated |
| Fresh control/deep retry loop | Implemented legacy harness |
| Primary-session selection and immutable diagnostic copy | Implemented, offline-tested (`session_selection.py`) |
| `run_eval.py` pass-throughs for `--load-trajectory`, mounts, agent-env | Implemented, offline-tested |
| Seed classification + rerun/rerun-deep method orchestration | Implemented, offline-tested and Harbor-verified end to end (`resume_experiment.py`); one real trial completed (all methods 0.0, see log) |
| Noninteractive skill contract (`AGENTDEBUG_TRAJECTORY_PATH`, `AGENTDEBUG_OUT_DIR`, `AGENTDEBUG_NONINTERACTIVE`) | Implemented in the skill (`SKILL.md`) |
| rerun-adamast method | Not implemented |
| rerun-skill (in-container AgentDebugX skill) method | Not implemented |

Do not report the legacy `control` and `deep` retries as rerun and
rerun-deep. They start fresh conversations and may make multiple retries, so
they do not yet satisfy this protocol.

## Evaluation population

Run one Seed attempt for each selected task. A task enters the recovery study
only when:

1. the task's oracle has passed in the recovery attempt's Harbor and environment setup;
2. Seed receives reward `0.0` from the hidden verifier; and
3. Seed has no harness, environment, agent-setup, or verifier exception.

Exclude oracle-invalid tasks and infrastructure failures. Record exclusions and
their failure class rather than treating them as unresolved agent attempts.

## Fixed configuration

The following must be identical across Seed and every recovery method for a task:

- Terminal-Bench dataset and task definition;
- Harbor version;
- backend-specific environment identity (cached SIF or resolved Docker image);
- Claude Code executable version and SHA-256;
- model, reasoning effort, timeout, and task instructions;
- fresh task filesystem; and
- maximum of one recovery attempt per method.

The shared `claude_installer.yaml` selects the Claude executable. Harbor trial
metadata must contain the resolved artifact path, version, SHA-256, and install
path so later YAML edits cannot change an existing job's recorded identity.

The preferred controlled comparison keeps the environment backend identical.
The harness also permits importing a completed Seed trajectory across backends
(for example, Singularity Seed to Docker recovery). Such runs must record both
the Seed and recovery backend, must have an Oracle-qualified recovery backend,
and must be reported separately from same-backend comparisons; the harness
warns but does not reject them.

## Methods

Every recovery method branches independently from the same completed Seed
native session and starts in a fresh task container.

| Method | Prior conversation | Added input or behavior | Purpose |
|---|---:|---|---|
| Seed | No | Original task only | One-shot baseline |
| rerun | Yes | Neutral verifier-failure notice | Measure retry headroom with retained context |
| rerun-deep | Yes | Host-generated AgentDebugX report | Measure static diagnosis-assisted recovery |
| rerun-adamast | Yes | Neutral notice plus frozen-taxonomy AdaMAST hooks and skill | Measure taxonomy-guided recovery |
| rerun-skill | Yes | Invoke AgentDebugX on the failed trajectory before correcting the task | Measure end-to-end skill-driven recovery |

rerun-deep and rerun-skill must diagnose the same immutable byte-identical
copy of Seed's native Claude session. The resumed session is a separate copy
because Claude appends the recovery turn to it.

## Execution sequence

1. Run Seed and wait for hidden verification.
2. Classify the result as resolved, eligible agent failure, or exclusion.
3. For an eligible failure, select the primary native Claude session while
   excluding subagent sessions.
4. Copy the completed session to immutable diagnostic storage and record its
   SHA-256.
5. Start each enabled recovery method from that same Seed session in a fresh task
   container.
6. Apply only the behavior defined for the method.
7. Run the hidden verifier once and preserve all setup, diagnosis, and task
   outcomes.

No recovery method may consume output from another recovery method. Follow-up
studies may evaluate repeated recovery, but those results must not be mixed
with this single-recovery protocol.

## Primary comparisons

| Comparison | Interpretation |
|---|---|
| rerun − Seed | Improvement from one resumed attempt |
| rerun-deep − rerun | Added value of a host-generated AgentDebugX diagnosis |
| rerun-adamast − rerun | Added value of frozen-taxonomy AdaMAST |
| rerun-skill − rerun | End-to-end AgentDebugX skill recovery headroom |
| rerun-skill − rerun-deep | In-agent skill workflow versus static host advice |

rerun-skill includes explicit diagnosis and reflection that rerun does not.
Report `rerun-skill − rerun` as product headroom, not as a causal estimate of
the debugger alone.

## Outcome classification

- **Resolved:** reward is `1.0` and no exception occurred.
- **Unresolved agent attempt:** reward is `0.0` and no exception occurred.
- **Infrastructure exclusion:** image conversion, environment start, agent
  setup, credential, diagnostic provisioning, or verifier execution failed.
- **Invalid task:** the oracle does not pass in the pinned environment.

A method is accepted only when its required behavior actually ran. For example,
rerun-adamast requires AdaMAST hook evidence and rerun-skill requires
native-trace evidence of AgentDebugX skill and CLI invocation.

## Measurements

Primary:

- resolved within the single recovery attempt;
- resolution-rate difference for each primary comparison.

Secondary:

- Claude prompt, completion, and cache tokens;
- Claude cost and wall time;
- AgentDebugX diagnosis tokens, cost, and latency;
- skill or hook invocation success;
- importer and diagnosis exit status;
- trajectory and report hashes; and
- whether the recovery used a materially different approach from Seed.

Report infrastructure exclusions separately and include denominators for every
method. Do not silently convert missing method evidence into an unresolved task.

## Acceptance criteria

- rerun through rerun-skill use the same failed Seed session and immutable
  diagnostic input.
- Every method uses the same pinned Claude executable, model, effort, environment,
  timeout, and recovery budget.
- rerun-deep and rerun-skill consume byte-identical diagnostic input.
- rerun-adamast uses the recorded frozen taxonomy and leaves no state in the
  graded workspace.
- rerun-skill invokes the injected skill and a real AgentDebugX CLI.
- AgentDebugX and AdaMAST artifacts stay outside the graded task workspace.
- Setup and method failures remain distinguishable from task failures.
- Claude and AgentDebugX usage and cost are recorded separately.
- Offline tests validate command construction and artifact selection without
  Harbor, SLURM, network access, or model credentials.

Implementation details and data flow are defined in [architecture.md](architecture.md).
Cluster commands and dated environment findings remain in
[TERMINAL_BENCH_EVAL.md](TERMINAL_BENCH_EVAL.md) and
[installer_findings.md](installer_findings.md).
