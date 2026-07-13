# Rerun Workflow

Rerun is the second major phase of AgentDebugX. It uses Diagnose output to
prepare controlled retry attempts, branch comparisons, and executor requests.

## When to use

Use Rerun when a failure has already been diagnosed and the system needs to test
whether a recovery strategy improves the run.

## Flow

1. Build a `RerunPlan` from a source trajectory and Diagnose results.
2. Convert the plan into a portable `RerunRequest`.
3. Select checkpoints, rollback points, or retry directives.
4. Dispatch work to an executor only when the caller explicitly requests
   execution and an approved executor is configured.
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

## Dependencies

Core request construction and branch comparison are local. Real execution
depends on an executor implementation and may require benchmark-specific
configuration, credentials, containers, or external services.

## Extension Rules

- Add executor integrations under `rerun/executors/`.
- Keep executor interfaces explicit about side effects.
- Do not hide network, filesystem, or benchmark execution behind Diagnose.
- Preserve deterministic branch comparison utilities for offline evaluation.
