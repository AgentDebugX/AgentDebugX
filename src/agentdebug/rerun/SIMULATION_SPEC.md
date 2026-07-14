# Simulated Rerun Contract

Simulated Rerun asks an LLM to generate one plausible corrected trajectory
from an existing trajectory and diagnostic report. It does not start the
original agent framework, invoke tools, inspect external state, or verify the
task outcome.

## CLI

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --simulate \
  --out rerun.simulated.json
```

The command uses the configured OpenAI-compatible model. Configure it once with
`agentdebug config set-llm`, or pass `--base-url`, `--api-key`, and `--model`.

## Inputs

The simulation prompt contains:

1. The original task goal and framework label.
2. The failed trajectory, bounded to the relevant events.
3. Detect, Attribute, and Recover diagnostic context.
4. The approved recovery directive.
5. The selected checkpoint policy.

For the CLI, simulation currently starts from the task beginning. The Python
API can build a hypothetical branch from a selected event.

## Model Output Contract

The model must return a complete JSON object:

```json
{
  "summary": "hypothetical outcome",
  "success": true,
  "events": [
    {
      "agent_name": "planner",
      "event_type": "plan",
      "step_index": 1,
      "output": "preserve the diagnosed constraint"
    }
  ]
}
```

`summary` must be non-empty, `success` must be a boolean, and `events` must be
a non-empty array of supported `AgentEvent` types. Every event must contain at
least one of `input`, `output`, or `error`.

If the response is truncated or invalid, the executor retries once with a
larger token budget. It also retries without `response_format` when a compatible
gateway does not support that parameter. Invalid output after the retry fails
the command instead of creating a partial trajectory.

## AgentDebugX Output

The CLI writes a Rerun workflow JSON artifact, not only a trajectory. Important
fields are:

```json
{
  "stage": "rerun",
  "status": "simulated",
  "execution_mode": "simulated_rollout",
  "executed": true,
  "live_execution": false,
  "verified": false,
  "plan": {},
  "evaluation": {
    "scope": "simulated_trajectory_only",
    "verified_task_outcome": false
  },
  "execution": {
    "metadata": {
      "artifact_type": "hypothetical_trajectory",
      "tools_executed": false,
      "model_claimed_success": true,
      "output_validated": true
    },
    "trajectory": {}
  },
  "diagnostic_report": {}
}
```

`executed=true` means that the simulation workflow called the model. It does
not mean the original agent or its tools ran. `model_claimed_success` is the
model's prediction, not a verified result. Local evaluation only compares error
signals in the original and hypothetical trajectories.

Every generated event includes:

```json
{
  "metadata": {
    "rerun_generated": true,
    "simulated": true,
    "observation_source": "model_generated"
  }
}
```

Do not use simulated output as evidence that a recovery fixed the task. Use a
live runtime-specific executor for that claim.
