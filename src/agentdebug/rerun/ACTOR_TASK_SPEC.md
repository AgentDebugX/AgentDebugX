# Actor Rerun Task Dataset

Actor task export is the non-executing Rerun mode for users who want to run
their own actor model in an application-owned environment. AgentDebugX creates
pending rollout tasks; it does not call a model or tool in this mode.

## CLI

JSONL is the recommended interchange format:

```bash
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --actor-task-format jsonl \
  --out rerun-tasks.jsonl
```

Parquet uses the same row schema and requires `pyarrow`:

```bash
pip install pyarrow
agentdebug rerun report.json \
  --trajectory trace.json \
  --plan-only \
  --actor-task-format parquet \
  --out rerun-tasks.parquet
```

Without `--actor-task-format`, `--plan-only` preserves the original auditable
workflow JSON output.

## Record Semantics

Each JSONL line or Parquet row is one pending actor rollout task. It is not a
completed SFT example and contains no response, label, reward, or verified
answer.

Core columns:

| Column | Meaning |
|---|---|
| `schema_version` | Actor task schema version, currently `1.0` |
| `record_type` | `agentdebug.rerun.actor_task` |
| `task_id` | Stable task identifier derived from the diagnostic report |
| `goal` | Original task goal |
| `messages` | Framework-neutral system/user messages for an actor adapter |
| `retry_directive` | Approved Recover output |
| `checkpoint_*` | Requested rerun policy and source location |
| `rerun_request_json` | Lossless canonical `RerunRequest` JSON |
| `diagnostic_context_json` | Detect, Attribute, and Recover context |
| `source_trajectory_json` | Complete original `AgentTrajectory` |
| `expected_output_schema_json` | Required live `AgentTrajectory` output |
| `required_capabilities_json` | Runtime capabilities required before dispatch |
| `status` | Always `pending` at export time |
| `requires_live_environment` | Always `true` |
| `verified` | Always `false` at export time |

Nested canonical objects are JSON strings in dedicated columns so JSONL and
Parquet share one stable schema across Python, SQL, Spark, and model-serving
systems. `messages` remains a native list of role/content objects for common
actor APIs.

## Actor Consumption

An actor adapter should:

1. Read one pending record.
2. Check `required_capabilities_json` against the selected runtime.
3. Restore or create the task environment.
4. Construct the actor from the project's framework and tools.
5. Apply `messages`, `retry_directive`, and checkpoint policy.
6. Execute a fresh rollout and record only observed events.
7. Write a new `AgentTrajectory` and retain `task_id` as provenance.
8. Run the task-specific evaluator before marking the result verified.

The actor must not return model-authored fictional tool observations. If the
runtime cannot restore a checkpoint, it should execute from the beginning and
record the effective policy instead of claiming checkpoint restoration.

## Training Data Boundary

These records are inference tasks, not training labels. After a real rollout is
evaluated, a separate curation step may join:

```text
pending actor task + observed trajectory + evaluator result
```

Only reviewed, successful joins should be transformed into SFT, preference, or
reinforcement-learning datasets. Keeping pending tasks separate prevents model
predictions and unverified recoveries from becoming ground truth.
