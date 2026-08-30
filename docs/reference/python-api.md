# Python API

The distribution is named `agentdebugx`; the Python package is `agentdebug`.

## Record and diagnose a run

`AgentDebug` is the high-level embedded entry point:

```python
from agentdebug import AgentDebug, EventType

debugger = AgentDebug()

with debugger.trace(
    goal="Book a refundable NYC to SFO flight",
    framework="my-agent",
) as trace:
    trace.record(
        EventType.PLAN,
        agent_name="planner",
        output="Search for the cheapest fares.",
    )
    trace.record(
        EventType.TOOL_RESULT,
        agent_name="browser",
        error="Checkout failed: refund_policy is required.",
    )

    report = trace.analyze()

print(report.summary)
```

When `step_index` is omitted, `TraceSession.record()` assigns increasing step numbers. Explicit values are preserved.

The context manager records a successful run end when the block exits normally. If an exception leaves the block, it records a failed terminal event and does not suppress the exception.

## Construct a portable trajectory

Use schema models when converting a custom framework:

```python
from agentdebug import AgentEvent, AgentTrajectory, EventType

trajectory = AgentTrajectory(
    task_id="task-42",
    goal="Complete checkout",
    framework="my-framework",
)

trajectory.add_event(
    AgentEvent(
        trace_id=trajectory.trace_id,
        agent_name="browser",
        event_type=EventType.TOOL_CALL,
        step_index=1,
        input={"tool": "checkout", "refund_policy": None},
    )
)
```

See the [Trace schema](../TRACE_SCHEMA.md) before adding framework-specific metadata.

## Run the local Diagnose pipeline

```python
from agentdebug import DiagnosePipeline

pipeline = DiagnosePipeline.local_default()
result = pipeline.run(trajectory)

print(result.report.summary)
print(result.attribution)
```

`DiagnosePipeline` accepts custom detector, attributor, and recoverer implementations. Passing `None` for an attributor or recoverer disables that sub-stage.

## Build a rerun plan

```python
from agentdebug.rerun import RerunWorkflow

workflow = RerunWorkflow.suggest_only()
rerun_result = workflow.run(
    report=result.report,
    trajectory=trajectory,
    execute=False,
)

print(rerun_result.plan.capability.reason)
```

Execution requires a configured `RerunExecutor` and an explicit `execute=True`. Simulation executors are rejected unless the workflow was created with simulation explicitly allowed.

## Serialize models

Use AgentDebugX helpers for Pydantic 1 and 2 compatibility:

```python
from agentdebug.schema import model_to_json, trajectory_from_json

payload = model_to_json(trajectory, indent=2)
restored = trajectory_from_json(payload)
```

For detailed fields, read [Trace schema](../TRACE_SCHEMA.md) and [Diagnostic report](diagnostic-report.md).
