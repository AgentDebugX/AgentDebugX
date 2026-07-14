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
- `LLMContinuationExecutor` is the built-in OpenAI-compatible executor. It
  performs a model rollout and returns a normalized `AgentTrajectory`.

## CLI and UI behavior

- `agentdebug rerun` performs a full-task rollout from the beginning by default.
- `agentdebug rerun --plan-only` builds the request without model execution.
- The web console performs the same model rollout from a user-selected event and
  stores the generated branch beside the original timeline.

The built-in executor generates an observable model trajectory from the task,
failed trace, and retry directive. It cannot restore arbitrary external tool
processes from an imported log. Live LangGraph, OpenAI Agents, benchmark, or
environment execution belongs in runtime-specific executors implementing the
same protocol.

## Dependencies

Core request construction and branch comparison are local. The built-in model
executor needs an OpenAI-compatible endpoint, API key, and model. Framework
executors may additionally require benchmark configuration, containers, or
external services.

## Extension Rules

- Add executor integrations under `rerun/executors/`.
- Keep executor interfaces explicit about side effects.
- Do not hide network, filesystem, or benchmark execution behind Diagnose.
- Preserve deterministic branch comparison utilities for offline evaluation.
