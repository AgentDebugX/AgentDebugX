# Validate with Rerun

Rerun tests a recovery proposal after Diagnose. It keeps planning, simulation, and real execution separate.

## Mode 1: build a plan

This is the safe default for a trajectory-only workflow:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --out rerun-plan.json
```

The plan includes the retry directive, checkpoint policy, approval metadata, and a capability assessment. A trace alone is normally missing the framework runner, tool runtime, and environment state needed for real execution.

Export the request for a separate actor pipeline:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --actor-task-format jsonl \
  --out rerun-tasks.jsonl
```

These rows contain pending actor inputs and provenance. They are not responses, verified outcomes, or training labels. Parquet output additionally requires `pyarrow`.

## Mode 2: generate a labeled simulation

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --simulate \
  --out rerun.simulated.json
```

Simulation asks the configured LLM to produce a hypothetical continuation. It executes no tools. Its output is marked `simulated`, and any evaluation is scoped to the simulated trajectory only.

!!! warning "Simulation is not validation"

    A plausible generated trajectory is not evidence that the task would succeed in the real application or benchmark.

## Mode 3: use a persistent live runner

The target application must provide a callback that owns its real model, tools, credentials, environment, and trajectory recorder. Start a runner service:

```bash
agentdebug runner serve my_project.runner:run_agent \
  --name my-agent \
  --framework langgraph \
  --host 0.0.0.0 \
  --port 8765 \
  --token-env MY_RUNNER_TOKEN
```

Save and verify the runner:

```bash
agentdebug config set-runner my-agent \
  --url http://127.0.0.1:8765 \
  --token-env MY_RUNNER_TOKEN \
  --default

agentdebug config doctor-runner my-agent
```

Then dispatch the rerun:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --out rerun.live.json
```

Select a named runner with `--runner NAME` when it is not the configured default.

## Branch from an event

`--start-event` uses a 1-based event position and resolves it to the event's stable ID:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --start-event 4 \
  --out rerun.from-event.json
```

The selected runner must advertise support for restoring or continuing from event checkpoints.

## Process compatibility transport

Local scripts and CI can use an application-owned command:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --runner-command "python path/to/project_rerun_runner.py" \
  --out rerun.json
```

The repository includes callback examples in `examples/http_agent_runner.py` and `examples/live_rerun_runner.py`.

## Execution proof

AgentDebugX accepts a live result only when the executor declares live execution and confirms that the returned trajectory was observed. Rerun then compares the source and returned branches with its local proxy evaluator.
