# Live Rerun Runner Protocol

AgentDebugX cannot reconstruct a real agent runtime from an exported trajectory.
A trajectory records observations; it does not contain executable tool code,
credentials, environment state, dependency versions, or side-effect targets.

Real Rerun therefore delegates execution to an application-owned agent runner.
The recommended transport is a persistent HTTP service; a local process
transport remains available for scripts and CI.

## What the Runner Represents

An agent environment is an executable system, not only a model URL. A live
runner owns:

```text
actor model + agent framework + prompts/memory + tool implementations
+ credentials + task inputs + environment state + execution loop + recorder
```

An OpenAI-compatible `/chat/completions` URL provides only the actor model. The
runner service URL represents the complete environment that can execute tools
and return observed trajectories.

## Persistent HTTP Runner

Implement a real callback in the application:

```python
from agentdebug.rerun import RerunRequest
from agentdebug.schema import AgentTrajectory

def run_agent(request: RerunRequest, source: AgentTrajectory, cancel_event):
    # Restore/create the task environment.
    # Construct the real framework actor and register its tools.
    # Apply request.directive and request.checkpoint.
    # Execute and record an observed AgentTrajectory.
    return {
        "execution": {
            "mode": "live_execution",
            "observed_execution": True,
            "tools_executed": True,
            "tool_execution_count": 3,
            "runner": "my_project.runner",
            "framework": "langgraph"
        },
        "trajectory": observed_trajectory.model_dump(mode="json"),
        "metadata": {"summary": "live rollout completed"}
    }
```

Start it once and keep it running:

```bash
export MY_RUNNER_TOKEN="<secret>"

agentdebug runner serve my_project.runner:run_agent \
  --name research-agent \
  --framework langgraph \
  --host 0.0.0.0 \
  --port 8765 \
  --token-env MY_RUNNER_TOKEN \
  --checkpoint-policy from_start \
  --max-concurrency 4
```

Configure the client once. AgentDebugX stores the token environment variable
name, never the token value:

```bash
agentdebug config set-runner research \
  --url http://127.0.0.1:8765 \
  --token-env MY_RUNNER_TOKEN \
  --default

agentdebug config doctor-runner research
agentdebug config list-runners
```

Subsequent reruns use the default runner:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --out rerun.live.json
```

Use `--runner NAME` to select a non-default configured service.

## HTTP API

Protocol version: `1.0`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Process health |
| `GET` | `/v1/capabilities` | Protocol/framework/checkpoint handshake |
| `POST` | `/v1/reruns` | Submit an asynchronous live rollout |
| `GET` | `/v1/reruns/{run_id}` | Poll `queued/running/succeeded/failed/cancelled` |
| `POST` | `/v1/reruns/{run_id}/cancel` | Request cooperative cancellation |
| `GET` | `/v1/reruns/{run_id}/trajectory` | Retrieve live proof and observed trajectory |

The client first reads capabilities and refuses unsupported protocol versions or
checkpoint policies before submitting work.

Transient connection failures, timeouts, `429`, `502`, `503`, and `504` are
retried with bounded exponential backoff. Submission carries the same unique
`submission_id` in the `Idempotency-Key` header and request body, so retrying a
timed-out POST cannot create a second rollout. After receiving `run_id`, client
timeout, network failure, or interruption triggers a best-effort cancel request.

### Capabilities

```json
{
  "protocol_version": "1.0",
  "submission_id": "submission_...",
  "runner": "my_project.runner",
  "framework": "langgraph",
  "live_execution": true,
  "checkpoint_policies": ["from_start", "from_event"],
  "environment_restore": true,
  "cancellation": true,
  "max_concurrency": 4
}
```

Do not advertise `from_event` unless the runtime can restore the required Agent
and environment state. Restarting from the task beginning while injecting a
diagnosed event is `from_start`, not checkpoint restoration.

### Submission

```json
{
  "protocol_version": "1.0",
  "request": {
    "trace_id": "source-trace",
    "checkpoint": {"policy": "from_start"},
    "directive": {"text": "apply the diagnosed correction"},
    "metadata": {"diagnostic_context": {}}
  },
  "source_trajectory": {}
}
```

The service responds with HTTP 202 and a `run_id`. AgentDebugX polls until a
terminal status, requests cooperative cancellation on timeout, then retrieves
the result. Repeating a submission with the same idempotency key returns the
same job instead of executing the actor twice.

### Live Result

```json
{
  "execution": {
    "mode": "live_execution",
    "observed_execution": true,
    "tools_executed": true,
    "tool_execution_count": 1,
    "runner": "my_project.runner",
    "framework": "langgraph"
  },
  "trajectory": {
    "trace_id": "source-trace__rerun_001",
    "goal": "original task",
    "framework": "langgraph",
    "events": [
      {"event_type": "tool.call", "input": {"query": "..."}},
      {"event_type": "tool.result", "output": {"result": "..."}}
    ]
  },
  "metadata": {"summary": "real rerun completed"}
}
```

AgentDebugX rejects missing execution proof, invalid trajectories, inconsistent
tool flags/counts, or a declared tool count larger than observed `tool.call`
events. Tool-free agents are valid with `tools_executed=false` and
`tool_execution_count=0`; they must still provide `observed_execution=true` and
an observed trajectory. The runner is a trusted execution boundary; proof is
auditable metadata, not a cryptographic attestation.

## Docker and Remote Environments

Run the HTTP service inside the same Compose/Kubernetes environment as the
agent. The service can keep models, browsers, databases, sandboxes, and tool
connections warm between reruns:

```text
AgentDebugX CLI/UI -> HTTP runner -> framework actor -> real tools/environment
                                      |
                                      -> observed AgentTrajectory
```

Expose only the runner port, configure health checks, and keep framework/tool
credentials inside that environment. For Kubernetes or job systems, the
callback may submit an internal job and wait for it; AgentDebugX still sees one
standard asynchronous HTTP job.

## Authentication and Security

- Use HTTPS outside localhost.
- Put bearer tokens in environment variables; do not store them in config.
- The CLI rejects URLs containing embedded username/password credentials.
- Treat source trajectories and diagnostic context as sensitive data.
- Apply authorization, network policy, rate limits, audit logs, and retention.
- Keep human/policy approval in front of side-effecting reruns.
- Implement cancellation checks at safe points inside long actor loops.
- Keep submission idempotency records at least as long as clients may retry.

## Process Compatibility Transport

`ProcessLiveExecutor` and `--runner-command` remain available. They pass request,
source trajectory, and result paths through environment variables. Use this for
local scripts and CI; use persistent HTTP for Docker, remote runtimes, warm
models, concurrency, cancellation, and repeated UI reruns.

## Runnable Example

`examples/http_agent_runner.py` performs a real configured model call and then
executes a deterministic local `lookup_policy` tool. It demonstrates the full
protocol but is not a universal Agent framework adapter. Replace its actor loop
and tools with the application's real implementation.

The built-in `create_http_runner_app()` keeps job state in process memory. It is
appropriate for a single runner process and development deployments. Production
services that require restart recovery or multiple replicas should implement
the same HTTP protocol over a durable job store/queue and route status/result
requests by `run_id`.

## Design Comparison

The persistent service split follows the same useful separation seen in
OpenTinker: lightweight HTTP clients, independently deployed environments, and
explicit scheduler/job lifecycle. AgentDebugX additionally adopts bounded
transient retries, cleanup on interruption, and idempotent submission. It does
not depend on OpenTinker's Ray/GPU training scheduler, `verl`, tensor transport,
reward-function upload, or training configuration because Rerun remains a
framework-neutral execution protocol.
