# First diagnosis

In about five minutes, you will normalize the repository's sample trace, diagnose it without an API key, inspect the report, and prepare a rerun plan.

**You will create:** `trace.json`, `report.json`, and `rerun-plan.json`.

## 1. Install AgentDebugX

```bash
python -m pip install agentdebugx
```

If you are working from a clone of this repository, use an editable install instead:

```bash
python -m pip install -e .
```

## 2. Normalize the sample

From the repository root:

```bash
agentdebug ingest examples/sample_trace.json \
  --format auto \
  --out trace.json
```

`trace.json` is an `AgentTrajectory`: a framework-independent sequence of normalized events.

!!! success "Expected result"

    The command exits successfully and creates `trace.json`. The sample keeps its committed trace ID, `trace_sample`.

## 3. Run local diagnosis

```bash
agentdebug diagnose trace.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out report.json
```

This command runs the three Diagnose stages:

```text
Detect → Attribute → Recover
```

- `--mode heuristic` selects deterministic rule-based detection.
- `--attributor heuristic` attaches local blame localization.
- `--recovery reflexion` adds a structured recovery proposal.
- `--out report.json` preserves the result for inspection or Rerun.

!!! success "Expected result"

    The command creates `report.json` with one finding. For the committed sample, the summary is:

    ```text
    Likely root cause: Tool execution error in search at step 2.
    ```

    The report localizes event `evt_2`, agent `search`, step `2`, and records heuristic attribution plus one recovery proposal.

## 4. Read the result

Open `report.json` and inspect these fields first:

| Field | Meaning |
| --- | --- |
| `summary` | Short diagnosis summary |
| `root_cause_event_id` | Stable event identifier selected as the root cause |
| `root_cause_step_index` | Step index associated with that event |
| `findings` | Failure modes, evidence, source event, and suggestion |
| `attribution` | Attributor output, when enabled |
| `recovery` | Recovery proposals, when enabled |

The relevant part of this sample report looks like:

```json
{
  "trace_id": "trace_sample",
  "root_cause_event_id": "evt_2",
  "root_cause_agent": "search",
  "root_cause_step_index": 2,
  "summary": "Likely root cause: Tool execution error in search at step 2."
}
```

You can also render a cascade-oriented terminal view:

```bash
agentdebug diagnose trace.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --traceback
```

## 5. Prepare a rerun request

A normalized trajectory and diagnostic report do not contain a live agent environment. Start by producing a plan:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --out rerun-plan.json
```

The plan explains which runtime capabilities are available and which are still needed for real execution. Continue with [Validate with Rerun](../guides/rerun.md) when you have an application-owned runner.

!!! success "Tutorial complete"

    You now have a normalized trajectory, an evidence-bearing diagnostic report, and an auditable rerun plan. No external model or tool execution was used in this tutorial.
