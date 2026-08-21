# Terminal-Bench 2.1 × AgentDebugX — historical setup and run notes

This is the host-specific discovery and validation log begun on 2026-08-07. It
preserves dated commands and results; it is not the current experiment
specification. See
[`EXPERIMENT_PROTOCOL.md`](EXPERIMENT_PROTOCOL.md)
for the canonical protocol and
[`architecture.md`](architecture.md) for the
current recovery design.

This log uses "trajectory.json"/"ATIF" loosely in places below (it predates
the terminology getting nailed down); see
[architecture.md's Terminology section](architecture.md#terminology-two-unrelated-things-are-both-called-trajectoryjson)
for the explicit definition — Harbor's ATIF (`agent/trajectory.json`, written
by Harbor) and AgentDebugX's own, unrelated `AgentTrajectory` schema
(produced by `agentdebug ingest`, conventionally also saved as
`*.trajectory.json`) are two different things that happen to share a
filename convention.

Update 2026-08-13: Harbor is pinned at 0.21.0. The scripts compile and the
required CLI remains available, but all real Singularity runs documented below
used 0.20.0. Re-run the oracle smoke test before collecting 0.21.0 agent data;
do not combine results across Harbor versions.

Update 2026-08-16 (node ccc0284, live SLURM allocation): **Harbor 0.21.0
oracle re-verified.** `./scripts/run_oracle.sh` over the then-current 11-task
subset: **11/11 resolved, 0 errored.** A separate 2-task ad hoc run
(`terminal-bench/torch-tensor-parallelism`, `terminal-bench/write-compressor`)
reproduced the known `torch-tensor-parallelism` harbor bootstrap crash
(`RuntimeError: Server process died`, image ships no `python3` — same
infrastructure exclusion documented below) and passed `write-compressor`
cleanly; it had previously been excluded as a "reproducible harness flake" and
is worth rechecking because it did not flake this time. 0.21.0 behaves
the same as the 0.20.0 results below for every task in the pinned subset;
0.21.0 agent data collection is now unblocked.

`harbor` runs from `~/.local/bin/harbor` (a self-contained `uv tool install`,
its own Python 3.12 venv) — it is **not** a package inside the `agent-debugx`
conda env, but works from any shell since `~/.local/bin` is on `PATH`.

Update 2026-08-16 (later same day): the `agentdebug` CLI resolved from `PATH`
now points at **this repo** (`pip install -e` from
`/u/yuchen85/agent-debug/official-agentdebugx`, version 0.3.1) rather than a
separate checkout — any AgentDebugX-side change made here is live for
container runs immediately, no `AGENTDEBUG_BIN` repointing needed. See
`docs/HANDOFF.md` for what differs between that other checkout's 0.2.12 and
this repo's 0.3.1.

Update 2026-08-16 (first real seed/rerun/rerun-deep trial): ran the full
resume pipeline end to end against real Harbor for the first time, on
`terminal-bench/raman-fitting`. `rerun-deep` produced a genuinely specific,
evidence-grounded diagnosis (curve fit converged to unphysical local minima
outside graphene's real Raman peak range, with a concrete fix). Outcome:
Seed, `rerun`, and `rerun-deep` all scored reward `0.0` (unresolved) — a
legitimate single-trial result, not a pipeline failure; no conclusion about
AgentDebugX's value should be drawn from one task. Getting the diagnosis to
that quality required fixing several bugs in the `agentdebug` package itself
(not this harness) — see `docs/HANDOFF.md` and `architecture.md`'s
"Reasoning-token starvation" section for details.

## 1. What runs TB 2.1

TB 2.x is no longer run by the old `tb` CLI — it runs on **Harbor**.

```bash
uv tool install 'harbor==0.21.0'
harbor run -d terminal-bench/terminal-bench-2-1 -a oracle -n-tasks 5     # smoke test
harbor run -d terminal-bench/terminal-bench-2-1 -a claude-code \
  -m anthropic/claude-opus-4-1 -n-concurrent 5
```

`claude-code` is a first-class built-in agent (`harbor.agents.installed.claude_code:ClaudeCode`).

### Environment blocker on this host

`docker` is **not installed** here; `apptainer`/`singularity` are. Harbor has a
first-class `singularity` backend (`src/harbor/environments/singularity/`) that
converts the task's docker image to `.sif` and talks to a FastAPI server inside
the container — built for exactly this (SLURM/HPC) case:

```bash
harbor run -d terminal-bench/terminal-bench-2-1 -a claude-code \
  --env singularity \
  --environment-kwarg singularity_image_cache_dir=/scratch/$USER/sif-cache
```

Alternative, no local containers at all: `--env daytona` (needs `DAYTONA_API_KEY`)
or `e2b`/`modal`/`gke`/`ec2` — all in `harbor.models.environment_type`.

**First thing to verify:** run the oracle smoke test under `--env singularity`.
If .sif conversion works there, the rest of the plan is unblocked.

### SLURM: harbor does not submit jobs — allocate a node yourself

There is no `sbatch`/`srun`/`squeue` anywhere in the harbor source. The
singularity backend assumes it is **already running on** a compute node (its
README diagram literally labels the host "SLURM node"), and
`singularity.py:342-345` notes that `--memory`/`--cpus` are deliberately not
passed to the container because cgroups aren't available on HPC — *"resource
limits should be enforced at the SLURM level instead (via `--mem`,
`--cpus-per-task`)."*

So: get an interactive allocation first, then run harbor inside it. Never on the
login node.

```bash
srun --partition=secondary-cs --time=04:00:00 --nodes=1 \
     --cpus-per-task=16 --mem=64G --pty /bin/bash
# then, inside the allocation:
harbor run -d terminal-bench/terminal-bench-2-1 -a claude-code --env singularity ...
```

`secondary-cs` caps at 4 h. Add `--mem` explicitly — without it you get the
partition default per-CPU, which may not cover N concurrent containers.

`--n-concurrent N` means N containers as child processes **on that one node**,
so size the allocation to N × per-task limits (below). Batch runs = wrap the
same command in an `sbatch` script.

**Egress: verified working** (2026-08-07, node `ccc0284`, partition
`secondary-cs`). From inside an allocation: `api.anthropic.com` 401,
`registry-1.docker.io` 401, `ghcr.io` 401 (401 = reached, unauthenticated),
`pypi.org` 200, `huggingface.co` 200. Same as the login node, no proxy vars.
So **no pre-pulling.** Harbor converts each image lazily when its trial starts
(~63 s cold, measured) and, with `singularity_image_cache_dir` set, the `.sif`
persists and is reused by every later run. The cache fills itself incrementally
from whatever subset you actually run. Bulk pre-pull only front-loads the same
wait, and is worth considering solely for a timed full-89 sweep.

**`.sif` conversion: verified working.** `apptainer 1.5.2`, unprivileged pull of
`docker://alexgshaw/sqlite-db-truncate:20251031` → 42 MB `.sif` in ~63 s, and
`apptainer exec --writable-tmpfs` into it runs fine (Debian 12, Python 3.13.7,
uid preserved). The `xattr ... ENOTSUP` warnings on Lustre are noise.

**Point every cache at scratch.** Home is quota'd (100 G soft, 34 G already
used); 89 TB images plus apptainer's build cache will blow through that.

These are recorded in `.env` (gitignored) — `set -a; . .env; set +a` before a run.
Defaults if unset, none of which are the cwd:

| cache | default | why it matters |
|---|---|---|
| `APPTAINER_CACHEDIR` | `~/.apptainer/cache` | eats the 100 G home quota |
| `APPTAINER_TMPDIR` | `$TMPDIR`, else `/tmp` | `TMPDIR` is unset here; `/tmp` is node-local, 46 G free |
| harbor `singularity_image_cache_dir` | fresh `tempfile.mkdtemp()` under `/tmp` | **per environment instance** — every trial re-converts the image (~60 s) and nothing is reused |

The cwd *does* collect two things: harbor's `jobs/` output dir, and the `.sif`
written by a manual `apptainer pull <name>.sif`. Point `--jobs-dir` at scratch
(harbor has no `HARBOR_JOBS_DIR` env var — only `HARBOR_VIEWER_JOBS_DIR`, and
only for `harbor view`).

**Disk budget (measured), for the full set — a subset costs proportionally
less (~3 GB for 20 tasks at the median):** all 89 TB 2.1 images sum to **21 GB compressed**
(median 150 MB; largest `reshard-c4-data` 1.4 GB, then two `qemu-*` at 1.3 GB).
SIF is compressed squashfs, so `.sif` ≈ the registry size 1:1 — the one image
pulled was 44.2 MB compressed → 43.2 MB `.sif`. Budget **~21 GB for the sif
cache**, plus roughly 2× that transiently in `APPTAINER_CACHEDIR` (blobs +
oci-tmp; 84 MB observed for that 43 MB image). `apptainer cache clean` after a
bulk pre-pull drops it back to ~21 GB steady state. Trivial on scratch's 10 TB;
would have been tight in `$HOME`.

### No GPU needed

All 89 tasks in `harbor-framework/terminal-bench-2-1` declare `gpus = 0`. Per-task
declared limits: `cpus = 1` (84/89; max 4), `memory_mb = 2048` (69/89; max 8192),
`storage_mb = 10240` each. The agent itself is API-based, so no local inference.

Also note the singularity backend *can't* allocate GPUs regardless — it returns a
default `EnvironmentResourceCapabilities()` (`gpus = False`) and never passes
`--nv`. Not a problem for TB 2.1; would be for a GPU dataset later.

## 2. What artifacts we get (this is what AgentDebugX eats)

### Host side

`JobConfig.jobs_dir` defaults to `Path("jobs")` — **relative to the cwd**. Always
pass `--jobs-dir $HARBOR_JOBS_DIR` (scratch). `jobs/` and `agentdebug-eval/` are
gitignored anyway, as a backstop for when the flag gets forgotten.

```
<jobs_dir>/<job>/
  config.json, result.json
  <task_id>/<trial>/
    agent/            # everything the agent writes
      trajectory.json   # ATIF v1.7
      sessions/         # raw Claude Code session JSONL
      claude-code.txt   # raw stream-json tee
      recording.cast
    verifier/         # test results, reward.txt / reward.json
    artifacts/        # files collected out of the environment + manifest.json
    config.json, lock.json, result.json, trial.log
```

(Multi-step tasks nest these under `steps/<step_name>/`; TB 2.1 is single-step.)

### Container side — and this is the part that matters for us

`EnvironmentPaths` (`harbor/models/trial/paths.py`) **bind-mounts** three host
dirs into the container:

| in container | ← mounted from |
|---|---|
| `/logs/agent` | `<trial>/agent/` |
| `/logs/verifier` | `<trial>/verifier/` |
| `/logs/artifacts` | `<trial>/artifacts/` |
| `/harbor/skills` | where `--skill` bundles land |

So anything written to `/logs/agent/...` inside the container appears on the
host immediately, with no collection step. `ClaudeCode.SUPPORTS_ATIF = True`
and it writes its own `trajectory.json` into that dir.

**Gotcha for method A3:** the skill's default output dir is `.agentdebug/` in the
cwd — which inside the container is the *task workspace*. Two problems: the
reports die with the container, and they litter the workspace the verifier then
inspects (a real risk of corrupting the result). In-container runs must write to
the mount instead:

```bash
agentdebug diagnose ... --out /logs/agent/.agentdebug/<name>.report.json
```

Cheapest fix is to say so in the injected instruction file; a skill-level
`AGENTDEBUG_OUT_DIR` honored by SKILL.md would be tidier.

Host-side methods (A2) should write **outside** `jobs/` so our artifacts are never
confused with harness output — e.g. `/u/yuchen85/scratch/agentdebug-eval/<job>/<task>/`.

Ingestion into AgentDebugX:
- **Ready today:** `agentdebug ingest` auto-detects `claude_code` format from
  the raw session JSONL (`_looks_claude_code_records` in
  `src/agentdebug/ingest/adapters/importers.py`). Point it at `agent/sessions/*.jsonl`.
- **Nice-to-have:** an `atif` importer for `trajectory.json` (uniform across
  claude-code / codex / gemini-cli / terminus-2 / openhands, and carries
  per-step tokens + tool observations). Small adapter, one new branch in
  `detect_payload_format`.

`harbor view jobs` gives a web viewer over the same data — useful for eyeballing
failures we diagnose.

## 3. Historical Harbor 0.20 trajectory-loading assessment

The following assessment predates the Harbor 0.21 architecture verification.
The current design uses Harbor 0.21 native-session loading as documented in
[`architecture.md`](architecture.md).

There is **no supported "here is a failed run, debug it and try again"** path:

- `--load-trajectory` (seed the agent from a prior ATIF trajectory) is declared
  in `cli/jobs.py` but the help text says *"Not implemented yet; reserved
  interface."*
- `--resume-trajectory` only resumes the agent's **own** native session across
  steps of one multi-step task. It cannot resume a different job's run.
- A trial's failure signal (`verifier/`) only exists **after** the agent
  finishes. Nothing inside `run()` can see pass/fail — TB tests are hidden.
  So any "retry on failure" logic must live *above* the harness, in a second
  `harbor run`. This is the constraint that shapes the whole design.

What is supported, and is enough:

| need | mechanism |
|---|---|
| put text in front of the agent | `--extra-instruction-path <file>` (repeatable) |
| put a **file** (the prior trajectory) in the container | `--mounts` / `--mounts-json` |
| give the agent the AgentDebugX skill | `--skill <path>` → copied to `$CLAUDE_CONFIG_DIR/skills/` |
| rerun only the failed tasks | `--include-task-name` (repeatable) / `--retry-include` |
| fair control method | `--n-attempts` (pass@k) |

So the shape is: **pass 1 → verifier → failed set → pass 2 with the prior
trajectory mounted + the skill injected + an instruction file that points at it.**

## 4. Recommended evaluation: 4-method retry study on the failed subset

Run TB 2.1 once with vanilla `claude-code`. Take the unresolved task ids. Rerun
*that same subset* four ways, identical model/budget, and compare resolve rate.

| method | what the second attempt gets | tests |
|---|---|---|
| **A0** control | nothing (plain retry) | how much is just "two tries" |
| **A1** raw context | the prior trajectory dumped into the instruction | how much is just "any hindsight" |
| **A2** report-only | AgentDebugX report text, diagnosed **on the host** | quality of the diagnosis itself |
| **A3** skill | trajectory mounted + `--skill agentdebug`; the agent runs `agentdebug diagnose` itself | the product: skill-driven RCA in the loop |

A0 and A1 are the methods that make the result meaningful — without them "A3 beats
one attempt" is unpublishable, since a second attempt alone lifts pass rate.

**Start with A0/A1/A2.** A2 needs *no* `agentdebug` inside the container (the
diagnosis runs on the host, only its text is injected), so it has near-zero
infra risk and already answers "does AgentDebugX's root cause help?". A3 adds
the in-container install + LLM creds and answers "does the *skill* work?".

Secondary metrics, all free from `result.json` / ATIF `final_metrics`:
resolve rate, tokens, wall-clock, and — for A3 — whether the agent actually
invoked the skill and whether its named root cause matches A2's.

### A3 concretely

`prior/<task>.trajectory.json` below is **AgentDebugX's own ingested
`AgentTrajectory`**, not Harbor's ATIF — `agentdebug ingest` builds it from
the native session `.jsonl`, never from Harbor's `agent/trajectory.json`.
The `--mounts .../trajectory.json` target path is only named that way by
convention; it is not the harbor ATIF file.

```bash
agentdebug integrations skill --target build/skills           # materialize the bundle
agentdebug ingest jobs/<job>/<task>/<trial>/agent/sessions/*.jsonl \
  --format claude_code --out prior/<task>.trajectory.json

harbor run -d terminal-bench/terminal-bench-2-1 -a claude-code \
  --include-task-name <task> \
  --skill build/skills/agentdebug \
  --mounts prior/<task>.trajectory.json:/mnt/prior/trajectory.json \
  --extra-instruction-path prompts/<task>.debug.md \
  --agent-env AGENTDEBUG_LLM_MODEL --agent-env AGENTDEBUG_LLM_API_KEY
```

`prompts/<task>.debug.md` (this file is where the eval lives, not in code):

> A previous attempt at this task failed. Its trajectory is at
> `/mnt/prior/trajectory.json`. Before doing anything else, use the
> `agentdebug` skill to diagnose it, then act on the recovery guidance.
> You are authorized to apply the fix in this workspace.

### Why not Design C (custom `ClaudeCode` subclass)

It cannot see the verifier result, so "attempt → detect failure → diagnose →
retry" inside one trial can only trigger on *self-assessed* failure. That is a
noisier and weaker claim than the two-pass design, for strictly more code. Keep
it in reserve for a later "self-repair without an oracle" experiment.

## 5. Small skill changes this needs (no refactor)

The current `SKILL.md` was written for an interactive host, and two rules block
a batch harness:

1. *"If the user has not provided a trajectory/export path, ask for one"* and
   *"Do not silently inspect host-local private state"* — there is no user to
   ask. Add a non-interactive entry: if `AGENTDEBUG_TRAJECTORY_PATH` is set (or
   a caller-named path is given in the instruction), use it without asking.
2. *"Do not apply fixes, rerun tools, or mutate a workspace unless the user
   explicitly approves"* — needed, and cheapest to satisfy from the **prompt**:
   the instruction file grants approval explicitly (see above). No code change.

This historical design used judge escalation when deterministic evidence was
weak. The current AgentDebugX skill and experiment protocol define the active
diagnosis policy.

`agentdebug rerun` today only emits a rerun *config* ("execution is
intentionally separated from diagnose"). Harbor is the rollout backend it was
waiting for; the glue is a script that renders the report into the
`--extra-instruction-path` file — thin, and it lives in `examples/`, not `src/`.

## 6. Environment checklist (runtime updated 2026-08-13)

Verified working:

- [x] SLURM — `srun` fine. Use `--partition=yongjoo` (128 cpus, 2 TB, idle,
      3-day limit) over `secondary-cs` (4 h, `PreemptMode=REQUEUE`, `GraceTime=0`).
- [x] `apptainer 1.5.2`; unprivileged docker→`.sif` conversion works (~63 s).
- [x] Compute-node egress: `api.anthropic.com`, docker/ghcr registries, pypi,
      huggingface, and the AgentDebugX LLM proxy (`/models` → 200) all reachable.
- [x] Caches + jobs dir pinned to scratch in `.env`.
- [x] `harbor 0.21.0` installed (`~/.local/bin/harbor`). The end-to-end results
      below were produced with 0.20.0 and require a new oracle smoke test.
- [x] AgentDebugX host pipeline: `ingest --format claude_code` (81 events) →
      `diagnose --mode judge` → real semantic root cause + recovery. This is the
      whole host half of method A2, already proven.

Remaining, in order:

1. **Anthropic credentials — the only hard blocker.** Nothing is set
   (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN` all
   unset). Harbor's `ClaudeCode` takes either:
   - an API key (preferred for a benchmark — no interactive rate limits), or
   - a subscription token: `claude setup-token` → set `CLAUDE_CODE_OAUTH_TOKEN`
     **and** `CLAUDE_FORCE_OAUTH=1` (without the latter the CLI prefers the key
     and the token is ignored — `claude_code.py:1565-1605`).

   A `~/.claude/.credentials.json` already exists on this host, so the token
   route is available. Caveat: subscription rate limits will throttle a
   multi-method sweep of long agentic runs; an API key with credits is the sane
   path for anything past the smoke test.

2. **Stabilize the AgentDebugX LLM endpoint.** `AGENTDEBUG_LLM_BASE_URL` is a
   `trycloudflare.com` **quick tunnel** — those rotate and die. It answers now,
   but a URL change mid-sweep silently kills every judge-mode diagnosis. Get a
   durable endpoint (named tunnel, or point at an API directly) before any run
   longer than a smoke test.

3. **Dataset fetch, untested.** `-d terminal-bench/terminal-bench-2-1` pulls
   from Harbor Hub on first use; whether it needs `HARBOR_API_KEY` is unknown
   until tried. The oracle smoke test answers this.

4. **End-to-end smoke, in this order** — each step is the cheapest thing that
   can fail next:
   ```bash
   srun --partition=yongjoo --time=02:00:00 --nodes=1 \
        --cpus-per-task=16 --mem=64G --pty /bin/bash
   set -a; . .env; set +a
   harbor run -d terminal-bench/terminal-bench-2-1 -a oracle -n-tasks 2 \
     --env singularity \
     --environment-kwarg singularity_image_cache_dir=$HARBOR_SIF_CACHE_DIR \
     --jobs-dir $HARBOR_JOBS_DIR                      # → 2/2 resolved
   # then swap -a oracle for: -a claude-code -m anthropic/claude-opus-4-1
   #   → verify <trial>/agent/{trajectory.json,sessions/} exist
   # then: agentdebug ingest that session → diagnose --mode judge
   ```
   Oracle first isolates harness/singularity failures from agent/credential
   failures. Only after oracle passes is a `claude-code` failure meaningful.

## 7. Smoke-test results (2026-08-07, node ccc0272, `--env singularity`)

The setup works. Oracle over 10 tasks: **7 resolved, 3 unusable**, in two
distinct failure classes worth keeping separate.

| class | tasks | meaning |
|---|---|---|
| oracle passes (reward 1.0) | write-compressor, sqlite-db-truncate, cancel-async-tasks, code-from-image, kv-store-grpc, openssl-selfsigned-cert, raman-fitting | usable |
| harbor bootstrap crash | torch-tensor-parallelism | **their bug** — image ships no `python3`; harbor apt-installs it, then all three pip-bootstrap paths fail → `FATAL: cannot bootstrap pip` → server dies. Deterministic. Not reproducible by hand with harbor's exact flags, so the cause is in harbor's invocation. Treat it as an infrastructure exclusion. |
| oracle scores 0.0, no exception | count-dataset-tokens, sqlite-with-gcov | container fine, oracle solution just doesn't pass its own verifier here. Unusable: an agent failure would be indistinguishable from an environment failure. |

Confirmed along the way: dataset fetch needs no `HARBOR_API_KEY`; `-i` task names
need the `terminal-bench/` prefix; per-trial reward lives at
`result.json → verifier_result.rewards.reward` (there is no `results.json`);
`.sif` reuse works (second run of a cached image skips conversion).

Harness lives in `examples/terminal_bench_eval/`. `run_eval.py run` launches a
job and prints its directory; `run_eval.py collect` writes one JSONL record per
trial containing task, reward, resolved/errored status, exception, trial
directory, trajectory, and sessions. Downstream methods read that record shape,
not Harbor internals.

## 8. First real result (2026-08-07)

**Pinned defaults** (`.env`, applied by `run_base.sh` to every method):
`TB_MODEL=anthropic/claude-sonnet-5`, `TB_EFFORT=medium`, `TB_MAX_RETRIES=3`.
Effort is passed as `--agent-kwarg reasoning_effort=…` so it is recorded in each
trial's `config.json` rather than depending on a CLI default.

The run below predates that pinning and used
`anthropic/claude-haiku-4-5` — chosen deliberately so failures would occur at
all. Treat it as a pipeline validation, not a benchmark number.

**Baseline (pass 1), 6 tasks:** 3 resolved, 3 failed, 0 errored. Every trial
produced `agent/trajectory.json` (ATIF) and `agent/sessions/*.jsonl`.

| resolved | failed |
|---|---|
| code-from-image, kv-store-grpc, openssl-selfsigned-cert | cancel-async-tasks, raman-fitting, sqlite-db-truncate |

The failed set is exactly the useful one: the oracle solves all three, so these
are genuine agent failures, not environment failures.

**Deep-debug diagnosis of `sqlite-db-truncate`** (`--mode deep --attributor
binary-search --recovery self-refine`): `DeepDebugAnalyzer` ingested 31 events
and localized the failure with 5 binary-search probes to **step 28**:

> Step 28 failed because it assumed the first byte after each `testwordXX`
> string was the row's value, but the surrounding hex shows SQLite record
> headers and type/length bytes between the text and the actual payload.

with a concrete next action ("do not infer the value from the byte immediately
after the word … extract the actual separate value field … verify on a few known
rows before writing `/app/recover.json`").

This is the quality bar the eval needs: task-specific, step-localized, and
actionable — not the generic boilerplate the heuristic mode produced on an
earlier fixture.

## 9. Suggested order of work

1. `uv tool install harbor`; `harbor run -d terminal-bench/terminal-bench-2-1 -a oracle -n-tasks 5 --env singularity` → verify: 5/5 resolved.
2. Same 5 tasks with `-a claude-code` → verify: `agent/trajectory.json` and `agent/sessions/*.jsonl` exist.
3. `agentdebug ingest` + `diagnose` one real failure on the host → verify: step-level root cause.
4. Full pass 1 on a subset (~40–60 tasks) → collect the failed set.
5. Methods A0/A1/A2 on that failed set → verify: a pass-rate delta table.
6. Method A3 (skill in-container) once A2 shows signal.

## Sources

- [How to run Terminal-Bench 2.1](https://www.tbench.ai/docs/run-terminal-bench-2-1)
- [Harbor — Agents](https://www.harborframework.com/docs/agents)
- [Harbor — Agent Trajectory Format (ATIF)](https://www.harborframework.com/docs/agents/trajectory-format)
- [Harbor — Run Evals](https://www.harborframework.com/docs/run-jobs/run-evals)
- [harbor-framework/harbor](https://github.com/harbor-framework/harbor) (source read: `cli/jobs.py`, `agents/installed/claude_code.py`, `skills.py`, `environments/singularity/`)
