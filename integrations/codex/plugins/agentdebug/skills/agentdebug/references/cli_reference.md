# AgentDebugX CLI Reference

Use `agentdebug ...` when installed. From a source checkout, use
`PYTHONPATH=src python -m agentdebug.cli ...`.

## Mental Model

The integration flow is:

1. `run`: resolve, normalize, diagnose, and persist one supplied trajectory or explicit batch.
2. `ui ensure`: start or reuse the local UI and return a run-scoped link.
3. `ingest` and `diagnose`: expert composable interfaces.
4. `rerun`: separately authorized second-stage execution.

The primary skill command is:

```bash
agentdebug run INPUT --profile standard --json
```

For project capture, normalized traces are immutable and grouped by exact host
session under `.agentdebug/sessions/<host>/<session-id>/traces/`. Diagnosis run
manifests live under the same session's `runs/` directory, reference their
input trace by identity and hash, and contain the diagnostic result. They do
not create a second trajectory snapshot.

Add `--ui` only when the user asks for an interactive inspection link.

`run` processes one trajectory. When `INPUT` is a multi-record
AgentErrorBench JSONL collection, select one record or process every independent
record:

```bash
agentdebug run INPUT.jsonl --trajectory-id TRAJECTORY_ID --profile standard --json
agentdebug run INPUT.jsonl --batch --profile standard --json
```

Batch `run` returns a summary whose `items` contain the normal per-trajectory
run results. It accepts independent rows from one directly supplied JSONL file,
or recursively discovers JSON files beneath a directory. A partial failure
exits with code 3. Do not pass `--ui` to a batch; select a returned `run_id`
and call `agentdebug ui ensure --run-id RUN_ID --json` when visual inspection
is requested.

GUI RCA collection processing remains under `python -m agentdebug.gui` because
it has separate OSWorld classification, failure filtering, parallel execution,
memory, and output semantics. Use unified `run` with `--profile gui --format
osworld` only for one OSWorld trajectory directory.

Profiles are `quick` (deterministic diagnosis), `standard` (deterministic
diagnosis plus local attribution/guidance), `deep` (LLM-backed DeepDebug), and
`gui` (LLM-backed CUA RCA). Use `--plan --json` to inspect resolution without
diagnosing or creating a completed report. Explicit `--format`, `--diagnoser`,
`--attributor`, and `--recovery` values are recorded as overrides.

Legacy public `agentdebug judge` and `agentdebug deep` commands are not the
preferred interface. Use `diagnose --mode judge` and `diagnose --mode deep`.

## Normalize: `ingest`

```bash
agentdebug ingest INPUT [--format FORMAT] [--out OUT]
```

Common examples:

```bash
agentdebug ingest external_trace.json --format auto --out .agentdebug/trajectory.json
agentdebug ingest session.jsonl --format hermes --out .agentdebug/hermes.trajectory.json
agentdebug ingest session.jsonl --format openclaw --out .agentdebug/openclaw.trajectory.json
agentdebug ingest osworld_run_dir --format osworld --out .agentdebug/osworld.trajectory.json
```

Supported formats:

| Format | Meaning |
|---|---|
| `auto` | Auto-detect input format. |
| `agenttrajectory` | Native AgentDebugX trajectory JSON. |
| `messages` | OpenAI/chat-style messages payload. |
| `message_list` | List of chat messages. |
| `conversations` | Rollout conversation format. |
| `event_list` | Generic event list. |
| `webshop_pages` | WebShop page/action logs. |
| `openai_agents_spans` | OpenAI Agents span dumps. |
| `crewai_events` | CrewAI event dumps. |
| `langgraph_callbacks` | LangGraph/LangChain callback logs. |
| `hermes` | Hermes CLI session export JSONL or native session object. |
| `openclaw` | OpenClaw session JSONL stream. |
| `osworld` | OSWorld/CUA trajectory directory. |

Use `--format auto` when the artifact is unambiguous. Use explicit formats for
host-native exports when the user names the host.

Useful overrides:

```bash
--trace-id TRACE_ID
--task-id TASK_ID
--goal GOAL
--framework FRAMEWORK
```

Validation after ingest:

```bash
test -s .agentdebug/hermes.trajectory.json
jq '.trace_id, .framework, (.events | length)' .agentdebug/hermes.trajectory.json
```

If `jq` is unavailable, use a small Python one-liner or inspect the JSON file
with the host's normal file-reading tool.

## Diagnose: `diagnose`

For regular modes, `diagnose` combines three choices:

- `--mode`: diagnosis engine.
- `--attributor`: root-cause localization attachment.
- `--recovery`: recovery proposal attachment.

The `diagnose` command currently requires all three flags explicitly.

```bash
agentdebug diagnose TRAJECTORY_OR_TRACE_ID \
  --mode MODE \
  --attributor ATTRIBUTOR \
  --recovery RECOVERY \
  [--traceback --no-color] \
  [--out REPORT]
```

Modes:

| Mode | Use | LLM required |
|---|---|---|
| `heuristic` | Fast deterministic rules. Run first. | No |
| `judge` | LLM judge diagnosis. | Yes |
| `deep` | DeepDebug iterative diagnosis. | Yes |
| `gui-rca` | OSWorld GUI root-cause analysis. | Yes, vision/tool-calling |

### DeepDebug option contract

DeepDebug is a complete Diagnose workflow, not an attributor that should be
combined with an independent recovery strategy. It performs global analysis,
structure-guided localization, candidate adjudication, and fix-guidance
generation internally.

With `--mode deep` or `--mode deepdebug`, the CLI automatically packages the
profile's fix guidance through `DeepDebugRecovery`. Use `--recovery deepdebug`
to request that packaging explicitly, or `--recovery none` to omit the
standard recovery payload. Do not combine DeepDebug with values such as
`binary-search` or `self-refine`, because that runs a second attachment after
the DeepDebug workflow and makes the result's ownership ambiguous.

Attributors:

| Attributor | Use | LLM required |
|---|---|---|
| `none` | No blame attachment. | No |
| `heuristic` | Deterministic localization. | No |
| `all-at-once` | One-pass LLM blame. | Yes |
| `step-by-step` | Candidate-by-candidate LLM blame. | Yes |
| `binary-search` | Interval-style LLM localization. | Yes |
| `counterfactual` | Counterfactual-style LLM localization. | Yes |

Recovery modes:

| Recovery | Use | LLM required |
|---|---|---|
| `none` | No recovery output. | No |
| `deepdebug` | Package DeepDebug's evidence-backed fix as a retry directive. | No additional call |
| `reflexion` | Next-run reflection hints. | No |
| `critic` | Verifier/guard suggestions. | No |
| `self-refine` | Critic/refiner next-action proposals. | Yes |
| `auto-manual` | Learned rule/manual suggestion. | Optional |
| `saga-rollback` | Side-effect rollback scaffold. | No, usually empty from CLI |

Deterministic first pass:

```bash
agentdebug diagnose .agentdebug/hermes.trajectory.json \
  --mode heuristic \
  --attributor none \
  --recovery none \
  --traceback --no-color \
  --out .agentdebug/hermes.traceback.txt
```

JSON report with deterministic recovery:

```bash
agentdebug diagnose .agentdebug/hermes.trajectory.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out .agentdebug/hermes.report.json
```

LLM judge escalation:

```bash
agentdebug diagnose .agentdebug/hermes.trajectory.json \
  --mode judge \
  --attributor all-at-once \
  --recovery critic \
  --out .agentdebug/hermes.judge.report.json
```

DeepDebug escalation:

```bash
agentdebug diagnose .agentdebug/hermes.trajectory.json \
  --mode deepdebug \
  --traceback --no-color \
  --out .agentdebug/hermes.deep.traceback.txt
```

Rule packs for heuristic mode:

```bash
--rule-pack auto
--rule-pack core
--rule-pack agenterrorbench
--rule-pack all
```

`--rule-pack` is repeatable.

## Batch Processing

Process every JSON file in a directory recursively, or every non-empty JSON
record in a JSONL file:

```bash
agentdebug run <directory-or-jsonl> --batch --profile standard --json

agentdebug batch ingest <directory-or-jsonl> --out-dir <trajectories>

agentdebug batch diagnose <directory-or-jsonl> \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out-dir <run-directory>
```

`run --batch` gives every successful item a durable run manifest and persists
it through the selected SQLite or JSONL store. `batch diagnose` is the lower-
level alternative that writes normalized trajectories and reports separately, plus a
`batch-summary.json` containing per-record status and errors. A partial failure
returns exit code `3` without deleting successful outputs.

Use batch mode only when each JSON file or JSONL line is an independent
record. Use regular `ingest` when one JSONL stream represents one trajectory.

## LLM Configuration

LLM-backed modes and attributors use explicit flags, saved config, or env vars:

```bash
agentdebug config set-llm \
  --base-url "$AGENTDEBUG_LLM_BASE_URL" \
  --api-key "$AGENTDEBUG_LLM_API_KEY" \
  --model gemini-3-flash
```

Env vars:

```bash
AGENTDEBUG_LLM_BASE_URL
AGENTDEBUG_LLM_API_KEY
AGENTDEBUG_LLM_MODEL
```

Default model when unset is `gemini-3-flash`. LLM-backed failures commonly
exit with code `4` when credentials are missing.

## Rerun

Configure a persistent application-owned HTTP runner once:

```bash
agentdebug config set-runner NAME \
  --url http://127.0.0.1:8765 \
  --token-env RUNNER_TOKEN \
  --default
agentdebug config doctor-runner NAME
```

Then CLI Rerun starts the application's real framework actor from the beginning
of the task:

```bash
agentdebug rerun REPORT \
  --trajectory TRAJECTORY \
  --out .agentdebug/rerun.json
```

The runner owns the model, tools, credentials, and environment and returns a
new observed trajectory with live-execution proof. Use `--runner NAME` to select
a non-default service, or `--runner-command` for local process compatibility.
A trajectory alone is not
executable; use `--plan-only` to inspect missing capabilities. `--simulate`
returns a validated hypothetical trajectory with `status=simulated`,
`tools_executed=false`, and `verified=false`; it cannot validate a fix. The web
console uses `AGENTDEBUG_RUNNER_URL` or `AGENTDEBUG_RERUN_COMMAND`; it defaults
to `from_start` and uses `from_event` only when the server administrator sets
`AGENTDEBUG_UI_RERUN_POLICY=from_event` for a capable runner.

Export a pending task for a user-owned actor runtime without executing it:

```bash
agentdebug rerun REPORT \
  --trajectory TRAJECTORY \
  --plan-only \
  --actor-task-format jsonl \
  --out rerun-tasks.jsonl
```

Use `parquet` instead of `jsonl` after installing `pyarrow`. Actor task rows
contain no response, label, reward, or verified answer.

## Stores

Diagnose the exact auto-captured trajectory for the calling Claude Code or
Codex session:

```bash
agentdebug run --current --profile quick --json
```

With valid session-scoped plugin context, bare `agentdebug run` is equivalent
to `--current`. It fails instead of selecting a recently modified trace when
that context is absent.

```bash
agentdebug list --store-jsonl .agentdebug/traces.jsonl
agentdebug show <trace_id> --store-jsonl .agentdebug/traces.jsonl
agentdebug inspect --store-jsonl .agentdebug/traces.jsonl
agentdebug diagnose <trace_id> --store-jsonl .agentdebug/traces.jsonl --mode heuristic --attributor none --recovery none
```

SQLite stores use `--store-sqlite PATH` instead of `--store-jsonl PATH`.

## Exit Codes

| Code | Meaning |
|---:|---|
| `0` | Success. |
| `1` | Unknown or unhandled command branch. |
| `2` | Bad input, missing store, parse/convert/load failure, or diagnosis failure. |
| `3` | Unknown `trace_id` in a store. |
| `4` | LLM or hub operation failure, often missing LLM credentials. |
| `5` | UI dependency missing for `inspect` / `serve`. |

## Integration Generation

```bash
agentdebug integrations skill --platform claude --target ~/.claude/skills --name agentdebug
agentdebug integrations skill --platform codex --target ~/.agents/skills --name agentdebug
agentdebug integrations skill --platform hermes --target ~/.hermes/skills/debugging --name agentdebug
agentdebug integrations skill --platform openclaw --target ~/.openclaw/skills --name agentdebug
agentdebug integrations install --platform claude
agentdebug integrations install --platform codex
agentdebug integrations status --platform codex --json
agentdebug integrations openhands-microagent --target .openhands/microagents --name agentdebug
```
