# AgentDebugX and AdaMAST Recovery Architecture
Status: proposed architecture, revised 2026-08-16. Target runtime: Harbor 0.21.0,
Terminal-Bench 2.1, and Claude Code.

## Decision
Add recovery methods in which Claude Code either uses AgentDebugX or runs AdaMAST
with a fixed failure-mode taxonomy. The recommended first version is
**post-failure resume with a completed prior trajectory**, not continuous
monitoring of the live attempt or cross-task taxonomy learning.

The outer retry loop remains responsible for detecting a real verifier failure.
It then starts a fresh container, resumes the failed Claude conversation, mounts
an immutable copy of the completed failed session, injects the AgentDebugX
skill, and explicitly asks Claude to diagnose and correct itself.

This preserves conversation context without preserving task-container state.
The resumed trajectory may cross environment backends when explicitly selected;
both the Seed and recovery backend are recorded.
It also avoids racing a JSONL file that Claude is actively appending.

The canonical method definitions, controls, measurements, and acceptance criteria
are specified separately in [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md).

## Terminology: two unrelated things are both called "trajectory.json"

- **Harbor ATIF** — Harbor's own [Agent Trajectory Interchange
  Format](https://www.harborframework.com/docs/agents/trajectory-format).
  Written directly by Harbor, unconditionally, to every trial's
  `agent/trajectory.json` (see "Verified capabilities" below). AgentDebugX
  never reads or writes this file in the current design.
- **AgentDebugX's `AgentTrajectory`** — a completely different schema
  (`trace_id`, `task_id`, `goal`, `framework`, `started_at`, `ended_at`,
  `events`, `metadata`), produced by `agentdebug ingest --format claude_code`
  from Claude's *native session* `.jsonl` — never from Harbor's ATIF. Despite
  the unrelated schema, callers conventionally still name the output file
  `*.trajectory.json` (see `run_eval.py`'s `cmd_diagnose` and
  `docs/TERMINAL_BENCH_EVAL.md`'s ingestion examples), which is the source of
  the naming collision. When precision matters, this document says "ATIF" for
  Harbor's format and "AgentTrajectory" (or "AgentDebugX's ingested
  trajectory") for AgentDebugX's — a bare "trajectory.json" is ambiguous.

## Assumptions and boundaries
- Every recovery attempt gets a fresh task container.
- Docker images are retained cache inputs: never prune or remove them.
  Completed Docker containers and Compose networks are removed with plain
  `docker compose down`; Harbor is invoked with `--no-delete` so it does not
  add `--rmi local` or `--volumes`.
- `--load-trajectory` restores conversation, not files.
- Terminal-Bench's hidden verifier is authoritative. Claude cannot know that it
  failed until Harbor finishes the attempt and the outer loop reads the reward.
- “Automatically correct itself when needed” therefore has two meanings:
  - **Reliable:** automatically invoke AgentDebugX on the next resumed attempt
    after a verifier-confirmed failure.
  - **Best effort:** invoke AgentDebugX inside an active attempt when Claude
    detects loops, repeated tool failures, or uncertainty. This cannot detect a
    silently wrong final answer before hidden verification.
- The benchmark task, verifier, model, effort, timeout, and recovery budget must
  remain identical across methods. Every method must use the same pinned Claude Code
  executable; record both its version and artifact hash.
- AdaMAST uses one immutable taxonomy across the evaluation. It must not learn
  from the evaluated tasks or carry mutable state between them.

## Verified capabilities

### Harbor 0.21.0
- `--skill` copies an injected skill into Claude's
  `$CLAUDE_CONFIG_DIR/skills/` before the run.
- Claude's config directory is `/logs/agent/sessions` in the container.
- Native sessions are written below
  `/logs/agent/sessions/projects/<cwd-slug>/*.jsonl`.
- `/logs/agent` is the trial's mounted agent-log directory and is visible to the
  running agent.
- Harbor creates `/logs/agent/trajectory.json` by converting the native Claude
  session in `populate_context_post_run`; this happens after Claude exits, so
  ATIF is not the right source for in-process self-debugging.
- `--load-trajectory` can losslessly seed a prior Claude native JSONL and resume
  it in a new trial. ATIF loading is portable but loses agent-specific details.
- Harbor lifecycle hooks expose phase events such as `AGENT_START` and
  `AGENT_END`, not per-tool-call events. Hooks alone cannot inject advice midway
  through one `claude --print` invocation.
- Claude Code's own hooks are different from Harbor lifecycle hooks. Claude
  hook input includes the exact `transcript_path` and `session_id`; a bounded
  `Stop` hook can return additional context or block stopping so Claude
  continues. Harbor can supply Claude settings containing those hooks.

### AgentDebugX
- The CLI already accepts Claude Code JSONL explicitly with
  `agentdebug ingest ... --format claude_code`.
- The Claude importer retains messages, thinking, tool calls, tool results,
  identifiers, and relevant session metadata.
- `agentdebug diagnose` expects a completed parseable input. Its JSONL reader
  does not ignore an incomplete trailing line, so a live log must be snapshotted
  at valid JSON-record boundaries.
- The generated skill currently assumes a trajectory path is provided, asks if
  it is absent, writes under `.agentdebug/`, and requires explicit permission
  before applying fixes. A noninteractive benchmark contract is needed.
- Injecting the skill does not install the `agentdebug` executable. Harbor and
  AgentDebugX provisioning are separate concerns.
- AgentDebugX does not currently expose an MCP server.

### AdaMAST
- The Claude integration consists of a guidance skill, lifecycle hooks, and a
  Python runtime. Skill injection alone does not activate the checkpoints.
- Its plugin normally bootstraps `uv` and AdaMAST during `SessionStart`, but
  that path needs `curl` or `wget`, downloads at trial time, and fails open.
- Harbor can inject the skill and pass Claude settings containing hooks. A
  pinned AdaMAST runtime must be staged before Claude starts.
- Hook state and traces can live under `/logs/agent/adamast`, outside `/app`.

## Recommended data flow: completed prior trajectory

```text
seed attempt
  -> Claude writes native session JSONL
  -> Harbor runs hidden verifier
  -> outer loop classifies reward=0 with no harness exception
  -> outer loop copies the completed JSONL to an immutable per-attempt path
  -> AgentDebugX skill bundle and CLI are provisioned in a fresh container
  -> Harbor loads the native JSONL to resume Claude's conversation
  -> the same completed JSONL is exposed separately as diagnostic input
  -> recovery prompt tells Claude to invoke AgentDebugX
  -> Claude reads the report, applies a new approach, and finishes the task
  -> Harbor verifies the recovery attempt
```

Use two representations of the same failed attempt:

1. `--load-trajectory` seeds Claude's conversation.
2. A separate read-only diagnostic copy is addressed by
   `AGENTDEBUG_TRAJECTORY_PATH`.

Do not diagnose the loaded session file in place. Claude appends the recovery
turn to its seeded session, making that copy live again. The immutable copy
guarantees deterministic diagnosis of exactly the failed seed.

## Trajectory acquisition options

### Option A — completed prior JSONL (recommended)

After verification, select the one primary Claude session JSONL, excluding
`subagents/`, copy it to a stable diagnostic input, and provide that path to the
next trial.

Advantages:

- Failure is confirmed by the real verifier.
- No concurrent-read race or partial JSON line.
- Exact diagnostic input can be hashed and recorded.
- rerun-deep and rerun-skill can consume the identical trajectory.
- The architecture is a small extension of the current retry loop.

Limitation: correction begins in a new attempt, not before the first attempt
ends.

### Option B — snapshot the active native session (feasible pilot)

Claude can invoke a helper from its skill while running. The helper locates the
newest primary JSONL under `$CLAUDE_CONFIG_DIR/projects/`, excludes subagent
logs, copies only complete JSON records into `/logs/agent/agentdebug/snapshots/`,
then ingests and diagnoses the snapshot.

When supported by the installed Claude Code version, the skill can use
`${CLAUDE_SESSION_ID}` to address
`$CLAUDE_CONFIG_DIR/projects/-app/${CLAUDE_SESSION_ID}.jsonl` exactly. Fall back
to newest-mtime discovery only when the session ID is unavailable.

Required helper behavior:

1. Resolve exactly one primary session or fail clearly.
2. Read line-by-line and stop before the first malformed/truncated record.
3. Write to a temporary file and atomically rename it.
4. Record source path, byte boundary, record count, and SHA-256.
5. Never include the diagnosis command's later output in that same snapshot.
6. Enforce a checkpoint watermark to prevent recursive repeated diagnosis.

Advantages: true within-attempt self-debugging with almost no Harbor changes.

Limitations:

- Claude decides when it is “needed”; hidden correctness is unavailable.
- The most recent action may not yet be flushed when the snapshot begins.
- Diagnosing one's own evolving transcript creates recursion and extra cost.
- The current AgentDebugX JSONL loader needs the boundary-safe helper.

Use this only after Option A establishes that the skill-driven recovery works.

### Option C — Claude Stop hook (strong automatic self-correction)

Provide Claude settings through Harbor with a bounded `Stop` hook. The hook
receives the exact `transcript_path`, creates a boundary-safe snapshot, runs
AgentDebugX, and—only when the report is actionable—returns additional context
and prevents Claude from stopping once so it can correct the attempt.

Guardrails:

- check Claude's `stop_hook_active` field;
- allow at most one or two diagnoses per attempt;
- keep a snapshot watermark and report hash;
- include the hook's `last_assistant_message` because the final message may not
  yet be present in JSONL;
- do not block stopping when diagnosis fails or finds nothing actionable.

This provides more deterministic automation than agent-selected skill use, but
it measures a **hook-driven debugger method**, not merely skill availability.
Keep it as a separate method rather than silently folding it into rerun-skill.

## Skill contract for batch recovery

Create a benchmark-specific wrapper around the canonical skill, or add a small
noninteractive section to it. The contract should be:

```text
AGENTDEBUG_TRAJECTORY_PATH=/mnt/agentdebug-input/failed.jsonl
AGENTDEBUG_OUT_DIR=/logs/agent/agentdebug
AGENTDEBUG_NONINTERACTIVE=1
```

When these are set, the skill must:

1. Use the named path without asking the user or searching private host state.
2. Verify the path exists and normalize it with `--format claude_code`.
3. Write every artifact under `AGENTDEBUG_OUT_DIR`, never the graded workspace.
4. Run deterministic diagnosis first and escalate according to the fixed method
   policy, not ad hoc model judgment.
5. Return a compact root cause, evidence location, and recovery action.
6. Immediately apply the recovery action because the experiment prompt grants
   permission and no interactive user exists.
7. Record whether the skill was invoked and which commands completed.

The prompt for rerun-skill should say, in substance:

> The prior attempt failed hidden verification. The filesystem is fresh, but
> your prior conversation is loaded. Use the AgentDebugX skill on the exact
> trajectory at `$AGENTDEBUG_TRAJECTORY_PATH` before editing. Read its evidence,
> choose a materially different approach, implement it, test it, and finish
> without asking for confirmation.

## Provisioning AgentDebugX inside the trial

Harbor's `--skill` installs Markdown skill instructions only. rerun-skill also
needs a working CLI and Python runtime.

Preferred approach:

1. Build a pinned AgentDebugX wheel from the experiment commit.
2. Store the wheel outside the graded workspace and expose it read-only.
3. Use a small custom Claude Code agent subclass or setup hook to install the
   wheel before Claude starts.
4. Run `agentdebug doctor` during setup and fail the trial as infrastructure
   error if provisioning fails.
5. Record AgentDebugX version and wheel hash in the trial summary.

Known compatibility issue: some Terminal-Bench images do not contain Python.
Those tasks need an infrastructure exclusion or a setup-time Python bootstrap;
do not let missing Python count as an agent failure.

## Provisioning AdaMAST inside the trial

Use the same custom Harbor Claude agent and pinned Claude artifact for every
method. rerun-adamast adds an execution layer; it must not use a different Claude
installer.

1. Stage a pinned AdaMAST runtime without APT or first-session downloads.
2. Inject its skill and direct Claude hook settings; do not install through the
   marketplace during a trial.
3. Preselect one frozen taxonomy and record its ID and SHA-256. The taxonomy
   must be created outside the evaluation task set.
4. Disable the selector, dashboard, generation, refinement, and learning
   subagents. Enable the required checkpoint hooks, including `SessionStart`,
   `PostToolUse`, `PostToolUseFailure`, and `Stop`.
5. Write all state to `/logs/agent/adamast` and treat missing hook evidence as
   an infrastructure failure, not a rerun-adamast result.

## Credentials and network

Heuristic diagnosis needs no separate LLM. Judge/deep diagnosis requires
`AGENTDEBUG_LLM_BASE_URL`, `AGENTDEBUG_LLM_API_KEY`, and
`AGENTDEBUG_LLM_MODEL` inside the agent environment, plus network access to the
configured endpoint.

For the experiment:

- Pass only the required `AGENTDEBUG_LLM_*` values through Harbor agent env.
- Never embed credentials in the skill, prompt, trajectory, or report.
- Ensure command logging masks keys.
- Pin one diagnosis mode per method. Do not let rerun-skill silently spend more
  LLM calls than rerun-deep.
- Capture AgentDebugX input/output/cache tokens and cost separately from Claude.
  This gap must be fixed before cost-aware results are collected.

## Reasoning-token starvation (found 2026-08-16, fixed same day)

Every LLM call in AgentDebugX's deep-mode pipeline (`aao_moe` attribution,
the deep-mode refine step, and every `--attributor`/`--recovery` LLM call)
hardcoded a small `max_tokens` (256-4096) that predates reasoning-model
support. Thinking models (Gemini 3.x, o-series) can spend the *entire*
requested budget on hidden reasoning tokens before ever emitting visible
text — measured directly against this project's `AGENTDEBUG_LLM_BASE_URL`
proxy: a trivial "reply with just: ok" prompt at `max_tokens=50` burned all
~47 tokens on reasoning and returned empty content, and a real Terminal-Bench
trajectory against `gemini-3.1-pro` used `reasoning_tokens=7866` against a
`max_tokens=4096` cap. This silently produced empty `findings[0].evidence`
and `.suggestion`, a generic templated `summary`, and an unstable localized
root-cause step (two runs of the identical input picked different steps)
across every deep-mode diagnosis run in this project before the fix. A
**stronger** model makes it worse, not better, since stronger models reason
more before answering.

No confirmed way exists to disable reasoning through this project's proxy:
`OpenAICompatClient.chat()`'s `thinking` parameter is accepted but never
sent; guessed `extra_body` overrides (`reasoning_effort`, `thinking_budget`,
a flat `thinking` key) had zero measured effect, and the one attempt using
Gemini's real nested `thinking_config` shape 400'd rather than working (the
right envelope for this specific gateway is unknown). The only confirmed fix
is raising `max_tokens` — to 16000 for full analysis calls, 2000 for the
binary-search-style yes/no probes — applied across every deep-mode LLM call
site in both `agentdebug` checkouts this project has touched. Any future
LLM-calling code added to AgentDebugX (a new attributor, recoverer, or
analyzer) must budget `max_tokens` the same way, or it will silently produce
empty output against any reasoning-enabled model.

## Orchestrator changes

Extend `run_eval.py` with narrow pass-throughs for:

- native trajectory loading;
- a read-only diagnostic-input mount;
- agent environment variables;
- the AgentDebugX-enabled custom agent, when selected.

Extend `retry_loop.py` to:

1. Add `seed`, `rerun`, `rerun-deep`, `rerun-adamast`, and `rerun-skill` method
   definitions.
2. Select one primary native Claude session deterministically.
3. Copy and hash the completed diagnostic input.
4. Load the prior session for every resume method.
5. Enable fixed-taxonomy AdaMAST only for `rerun-adamast`; inject AgentDebugX
   only for `rerun-skill`.
6. Classify setup/import/credential failures as harness failures.
7. Carry each method forward from its own most recent failed session.
8. Stop early on success while preserving equal maximum retry budgets.

Do not put these policies into Harbor or change Terminal-Bench task definitions.
They belong in the evaluation glue under `examples/terminal_bench_eval/`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Skill availability mistaken for skill use | Check native trace for Skill/Bash invocation and AgentDebugX artifacts |
| Resume file becomes live and nondeterministic | Diagnose a separate immutable copy |
| Partial live JSONL | Boundary-safe atomic snapshot helper |
| Hidden failure unavailable mid-run | Use verifier-triggered next-attempt recovery as primary design |
| rerun-skill combines skill value with extra reflection/compute | Report it as product headroom, not a causal debugger-only effect |
| AdaMAST silently fails open | Require hook evidence before accepting rerun-adamast |
| Taxonomy leaks evaluation outcomes | Freeze and hash a taxonomy built outside the evaluated tasks |
| CLI install fails | Setup preflight; classify as infrastructure failure |
| Reports contaminate graded files | Write only under `/logs/agent/agentdebug` |
| Recursive self-diagnosis | Snapshot watermark and one diagnosis per checkpoint |
| Diagnosis methods spend unequal compute | Pin mode, model, retry budget, and diagnosis-call budget |
| AgentDebugX secrets leak into logs | Agent-env injection, masking, and post-run secret scan |

## Implementation sequence

1. Add offline tests for the current evaluation scripts.
2. Add AgentDebugX token/cost accounting.
3. Implement native-session selection and immutable diagnostic copies.
4. Add load/mount/env pass-throughs and resume controls.
5. Add the noninteractive skill contract and output directory.
6. Provision a pinned AgentDebugX wheel through a custom agent setup.
7. Implement seed, rerun, rerun-deep, rerun-adamast, and rerun-skill with
   offline command-construction tests.
8. Run a no-LLM heuristic smoke test on a synthetic Claude JSONL.
9. Run the Harbor 0.21 oracle/Singularity smoke test.
10. Pilot one verifier-confirmed failed Seed across rerun, rerun-deep,
    rerun-adamast, and rerun-skill.
11. Only after the post-failure design works, prototype live Option B.

## Sources

Primary integration sources:

- [Claude Code agent implementation](https://github.com/harbor-framework/harbor/blob/v0.21.0/src/harbor/agents/installed/claude_code.py)
- [Trial environment paths](https://github.com/harbor-framework/harbor/blob/v0.21.0/src/harbor/models/trial/paths.py)
- [Trial lifecycle hooks](https://github.com/harbor-framework/harbor/blob/v0.21.0/src/harbor/trial/hooks.py)
- [Loading trajectories](https://github.com/harbor-framework/harbor/blob/v0.21.0/docs/content/docs/run-jobs/load-trajectory.mdx)
- [AdaMAST native plugin](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST/tree/main/plugins/adamast)
- [Claude Code skills: session-id substitution](https://code.claude.com/docs/en/skills#available-string-substitutions)
- [Claude Code hooks: transcript path and Stop behavior](https://code.claude.com/docs/en/hooks#stop)

AgentDebugX primary sources:

- [`agentdebug` skill](../../../src/agentdebug/integrations/agentdebug_skill/SKILL.md)
- [Claude Code importer](../../../src/agentdebug/ingest/adapters/importers.py)
- [Evaluation runner](../run_eval.py)
- [Retry loop](../retry_loop.py)
