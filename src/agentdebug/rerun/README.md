# Rerun Workflow

Rerun is the second major phase of AgentDebugX. It uses Diagnose output to
prepare controlled retry attempts, branch comparisons, and executor requests.

## When to use

Use Rerun when a failure has already been diagnosed and the system needs to test
whether a recovery strategy improves the run.

## Flow

Rerun consumes a bounded diagnostic context containing Detect findings, the
primary Attribution result, the selected Recovery proposal, and the normalized
root-cause location. Runtime executors receive this context together with the
approved retry directive and source trajectory.

1. Build a `RerunPlan` from a source trajectory and Diagnose results.
2. Convert the plan into a portable `RerunRequest`.
3. Select checkpoints, rollback points, or retry directives.
4. Dispatch work only when the caller requests execution and a compatible
   executor is configured.
5. Compare source and rerun branches with local evaluators.
6. Store or report the branch comparison result.

## Core API

- `build_rerun_request(report, trajectory=None)` converts a diagnostic report
  into the executor-facing request artifact.
- `RerunWorkflow.plan(report, trajectory=None)` creates an auditable plan
  without executing anything.
- `RerunWorkflow.run(report, trajectory, execute=False)` returns a plan by
  default. With `execute=True`, it requires a configured executor and evaluates
  the returned branch.
- `RerunExecutor` is the protocol implemented by runtime-specific backends.
- `ProcessLiveExecutor` invokes an application-owned framework runner that owns
  the real model, tools, credentials, and environment.
- `HttpLiveExecutor` connects to a persistent application-owned runner, performs
  a version/capability handshake, submits an asynchronous job, polls status,
  retries transient transport failures with bounded backoff, requests
  cancellation after timeout, network failure, or interruption, and validates
  its observed trajectory. Idempotent submission prevents retries from starting
  duplicate actor runs.
- `create_http_runner_app()` wraps a project callback with the standard runner
  API, bearer authentication, bounded concurrency, and cooperative cancellation.
- `LLMContinuationExecutor` is simulation-only and is rejected unless the
  caller explicitly enables simulation.
- `SimulatedRerunExecutor` is the explicit public name for that implementation;
  `LLMContinuationExecutor` remains as a compatibility alias.

## CLI and UI behavior

- Mode 1, request only: `--plan-only` writes workflow JSON, or pending actor
  tasks with `--actor-task-format jsonl|parquet`.
- Mode 2, simulation: `--simulate` generates a labeled hypothetical trajectory
  without executing tools or proving task recovery.
- Mode 3, live execution: a persistent HTTP runner or process callback performs
  a real rollout and returns observed events plus execution proof.
- A configured default HTTP runner performs the normal live full-task rollout.
- `agentdebug rerun --runner NAME` selects another persistent runner.
- `agentdebug rerun --runner-command ...` preserves the local process transport.
- `agentdebug rerun --plan-only` builds the request without model execution.
- `--plan-only --actor-task-format jsonl|parquet` exports a pending actor
  rollout task dataset for execution in a user-owned runtime.
- `agentdebug rerun --simulate` produces a labeled non-tool simulation.
- The web console prefers server-side `AGENTDEBUG_RUNNER_URL`, falls back to
  `AGENTDEBUG_RERUN_COMMAND`, and never accepts either value from a browser
  request. Checkpoint rerun requires explicit server policy and runner support.

Simulation-generated trajectories inherit reusable source context, but remove stale
failure-only metadata such as `expected_outcome`, expected root-cause labels,
failure-family labels, and fixture scenario markers. Business metadata and
trace-format hints remain available. When a generated trajectory contains a
`run.end` event, its timestamp becomes the trajectory's `ended_at`; incomplete
rollouts without a terminal event leave `ended_at` unset.

Trajectory-only uploads are not executable by themselves. See `RUNNER_SPEC.md`
for capability levels, HTTP lifecycle, idempotency, deployment, live execution
proof, and framework integration rules.
See `SIMULATION_SPEC.md` for the simulation prompt inputs, model JSON contract,
retry behavior, and output artifact.
See `ACTOR_TASK_SPEC.md` for the pending actor task schema and the boundary
between rollout inputs and verified training data.

## Dependencies

Core request construction and capability assessment are local. Live runners
may require model access, tool credentials, containers, benchmark state, or
external services owned by the integrating application.

## Extension Rules

- Add executor integrations under `rerun/executors/`.
- Keep executor interfaces explicit about side effects.
- Do not hide network, filesystem, or benchmark execution behind Diagnose.
- Preserve deterministic branch comparison utilities for offline evaluation.
