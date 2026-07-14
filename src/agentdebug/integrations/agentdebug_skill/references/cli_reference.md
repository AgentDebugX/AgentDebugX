# AgentDebugX CLI Reference

Use `agentdebug ...` when installed. From a source checkout, use
`PYTHONPATH=src python -m agentdebug.cli ...`.

## Mental Model

The CLI flow is:

1. `ingest`: normalize external traces into AgentDebugX `AgentTrajectory` JSON.
2. `diagnose`: run diagnosis, optional attribution, and optional recovery.
3. `rerun`: prepare a second-stage retry from a diagnostic report.
4. `inspect`, `list`, `show`: inspect stored traces.

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
agentdebug batch ingest <directory-or-jsonl> --out-dir <trajectories>

agentdebug batch diagnose <directory-or-jsonl> \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out-dir <run-directory>
```

`batch diagnose` writes normalized trajectories and reports separately, plus a
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

CLI Rerun starts a fresh rollout from the beginning of the task:

```bash
agentdebug rerun REPORT \
  --trajectory TRAJECTORY \
  --base-url "$AGENTDEBUG_LLM_BASE_URL" \
  --api-key "$AGENTDEBUG_LLM_API_KEY" \
  --model "$AGENTDEBUG_LLM_MODEL" \
  --out .agentdebug/rerun.json
```

The command calls the configured model and returns a new normalized trajectory.
Use `--plan-only` only when execution is intentionally deferred. The web console
can rerun from a selected event; CLI currently reruns the full task.

## Stores

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
agentdebug integrations skill --platform hermes --target ~/.hermes/skills/debugging --name agentdebug
agentdebug integrations skill --platform openclaw --target ~/.openclaw/skills --name agentdebug
agentdebug integrations openhands-microagent --target .openhands/microagents --name agentdebug
```
