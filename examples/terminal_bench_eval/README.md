# Terminal-Bench 2.1 × AgentDebugX

Harness for measuring whether an AgentDebugX deep-debug diagnosis makes a failed
Claude Code run succeed on retry. See the
[host-specific setup and run notes](docs/TERMINAL_BENCH_EVAL.md) for background.

Must run inside a SLURM allocation — Harbor does not submit jobs itself.
Harbor is pinned at 0.21.0 and re-verified there: `./scripts/run_oracle.sh`
resolved 11/11 of `tasks.txt` on 2026-08-16 (see
[TERMINAL_BENCH_EVAL.md](docs/TERMINAL_BENCH_EVAL.md)). `harbor` runs from
`~/.local/bin/harbor` (a standalone `uv tool install`), not from inside the
`agent-debugx` conda env.

```bash
srun --partition=yongjoo --time=04:00:00 --nodes=1 \
     --cpus-per-task=16 --mem=64G --pty /bin/bash
set -a; . .env; set +a
```

## Pieces

| file | role |
|---|---|
| `run_base.sh` | sourced by every `run_<method>.sh`/`run_batch.sh` script: env + SLURM guard + `run_arm`/`run_resume` helpers |
| `run_oracle.sh`, `run_baseline.sh`, `run_retry_control.sh`, `run_retry_deep.sh` | one script per legacy arm; each sets that arm's explicit flag overrides and calls `run_base.sh`'s `run_arm` helper |
| `run_seed.sh`, `run_rerun.sh`, `run_rerun_deep.sh`, `run_batch.sh` | thin wrappers over `resume_experiment.py` for one recovery method / one task, or many tasks at once — same underlying tool, see below |
| `run_eval.py` | `run` (launch a harbor job), `collect` (one JSONL record per trial), `diagnose` (ingest → deep diagnose → advice markdown) |
| `retry_loop.py` | the legacy experiment: repeated attempts for one task, `--arm control` vs `--arm deep` |
| `session_selection.py` | select a Seed trial's one primary Claude session (excluding subagents) and copy it to an immutable, hashed diagnostic-input path for rerun-deep/rerun-skill |
| `resume_experiment.py` | for one or many tasks: reuse-or-launch Seed, classify it, and run the requested recovery method(s) (`seed`, `rerun`, `rerun-deep`) — no separate batch mode, a single task is just a list of length one |
| `docs/EXPERIMENT_PROTOCOL.md` | canonical seed/rerun/rerun-deep/rerun-adamast/rerun-skill treatments, controls, measurements, and acceptance criteria |
| `docs/architecture.md` | recovery-system boundaries, data flow, provisioning, and future designs |
| `tasks.txt` | task subset whose oracle passes under singularity; exclusions documented inline |
| `tasks_preflight_25.txt` | 25-task candidate pool: 11 oracle-confirmed plus 14 static Apptainer candidates |
| `pinned_claude.py` | Harbor `ClaudeCode` subclass that installs one pinned host artifact without APT |
| `install_matrix.py` | Collect install-only results and classify setup failures |
| `tasks_installer_matrix.txt` | 11 oracle-verified images plus six prior APT installer failures |

Pinned-installer implementation and validation are complete. See the
[installer findings](docs/installer_findings.md) for the artifact hash,
17-image matrix, and exact failure classifications. This does not change the
status of resume/rerun-skill below.

## Choose a Claude Code version

Edit `claude_installer.yaml` once for the experiment. It is the shared contract
for every arm:

```yaml
version: '2.1.233'
artifact_path: /shared/artifacts/claude-code-2.1.233-linux-x86_64
artifact_sha256: 55d281096f57d411ebbdd94dbf5e9ff3accb7c05713e37348c2c11d4b83bf9d9
install_path: ~/.local/bin/claude
```

`artifact_path` may be absolute or relative to the YAML file. The artifact must
be a native Linux x86-64 Claude Code executable; verify its version and record
the output of `sha256sum` in the YAML. `install_path` must remain below `~/` so
installation stays unprivileged. A custom location is linked from
`~/.local/bin/claude` to preserve Harbor's inherited Claude behavior.

Pass the config to the existing runner; it selects the custom agent
automatically. At launch, the runner validates the artifact and copies the
resolved version, absolute artifact path, hash, and install path into Harbor's
trial config. Later edits to the YAML therefore cannot change the recorded
identity of an existing job.

```bash
python examples/terminal_bench_eval/run_eval.py run \
  --arm pinned-claude-install \
  --task sqlite-db-truncate \
  --model anthropic/claude-sonnet-5 \
  --jobs-dir "$HARBOR_JOBS_DIR" \
  --sif-cache-dir "$HARBOR_SIF_CACHE_DIR" \
  --install-only \
  --claude-installer-config examples/terminal_bench_eval/claude_installer.yaml
```

## Experiment and implementation status

The canonical seed/rerun/rerun-deep/rerun-adamast/rerun-skill design,
fairness controls, measurements, and exclusion rules are in
[the experiment protocol](docs/EXPERIMENT_PROTOCOL.md). The supporting
recovery design is in [the architecture](docs/architecture.md).

| Capability | Status |
|---|---|
| Configurable pinned Claude installer | Implemented and validated |
| Install-only compatibility matrix | Implemented and validated |
| Fresh control/deep retry loop | Implemented legacy harness |
| Primary-session selection and immutable diagnostic copy | Implemented, offline-tested (`session_selection.py`) |
| `run_eval.py` resume pass-throughs (`--load-trajectory`, `--mount`, `--agent-env`) | Implemented, offline-tested |
| Seed classification + rerun/rerun-deep arm orchestration | Implemented, offline-tested and Harbor-verified (`resume_experiment.py`); one real trial completed (`raman-fitting`: seed/rerun/rerun-deep all 0.0) |
| Noninteractive skill contract | Implemented in the skill (`SKILL.md`) |
| rerun-adamast treatment | Not implemented |
| rerun-skill (in-container AgentDebugX skill) treatment | Not implemented |

## Seed/rerun/rerun-deep resume orchestration

One tool, `resume_experiment.py`, covers one task or many — there is no
separate batch mode, and no separate "launch Seed first" step: give it a
task and it reuses an existing Seed trial if you point it at one, otherwise
it launches Seed itself.

```bash
TB_TASK=terminal-bench/sqlite-db-truncate ./scripts/run_seed.sh        # seed only, no recovery method
TB_TASK=terminal-bench/sqlite-db-truncate ./scripts/run_rerun_deep.sh  # launches its own seed, then rerun-deep
./scripts/run_batch.sh                                                 # the same tool, every task in tasks.txt
```

Each script is a thin wrapper setting `resume_experiment.py`'s `--method`
(`seed`, `rerun`, or `rerun-deep`) and calling `run_base.sh`'s `run_resume`
helper. `resume_experiment.py` classifies the (reused-or-fresh) Seed result;
if it's an eligible failure (reward `0.0`, no exception), it selects the
primary session, copies it to an immutable diagnostic input under
`$AGENTDEBUG_EVAL_DIR/<task>/<seed-trial-name>/diagnostic-input/`, and runs
the requested method(s) loading that identical diagnostic input via
`--load-trajectory` — `rerun` and `rerun-deep` differ only in the treatment
text passed as `--extra-instruction-path`. Every artifact for one Seed run
lives under that same `<task>/<seed-trial-name>/` dir, so re-running a task
never collides with a prior run.

To reuse an already-completed Seed trial instead of paying for a fresh one,
set `TB_SEED_TRIAL_DIR` (single task) or use `--tasks-config` (below, for a
mix of fresh and reused tasks). A reused trial's recorded model/effort/Claude
build is checked against the current run's config first — a mismatch aborts
the whole run rather than silently mixing configurations
(`EXPERIMENT_PROTOCOL.md`, "Fixed configuration").

`agentdebug` on `PATH` is this repo's own editable install (see
[TERMINAL_BENCH_EVAL.md](docs/TERMINAL_BENCH_EVAL.md)) — `--mode deepdebug`
needs no `--attributor` (dead for deep mode; `cmd_diagnose` passes `none`)
but does need `--recovery` (`reflexion` by default, whose output overwrites
the report's `suggestions`).

### Many tasks, one call

```bash
./scripts/run_batch.sh                                              # every task in tasks.txt
TB_TASKS="raman-fitting cancel-async-tasks" ./scripts/run_batch.sh   # a subset, all fresh seeds
TB_TASKS_CONFIG=tasks_with_reuse.yaml ./scripts/run_batch.sh         # mix fresh + reused seeds
```

`--tasks-config` takes a YAML list; entries are either a bare task name
(fresh Seed) or `{task, seed_trial_dir}` to reuse an existing trial for just
that task:

```yaml
tasks:
  - terminal-bench/sqlite-db-truncate
  - task: terminal-bench/raman-fitting
    seed_trial_dir: /u/yuchen85/scratch/harbor-jobs/2026-08-16__16-34-05/raman-fitting__a6Bqv7g
```

Output is one aggregate JSON array to stdout (or `--out <path>`), one row
per task: `{task, seed_outcome, seed_reward, seed_trial_dir, methods}` (a
`resolved`/`exclusion` row has no `methods` key — no recovery method ran).
This is what turns single-trial anecdotes (like the one `raman-fitting`
result in `TERMINAL_BENCH_EVAL.md`) into an actual resolve-rate comparison
across a task population, per `EXPERIMENT_PROTOCOL.md`'s evaluation
population. Note this still burns real Claude/AgentDebugX usage per task —
size the task list to your budget. `rerun-adamast` and `rerun-skill` are not
implemented yet.

## Current legacy harness

```bash
./scripts/run_oracle.sh                          # setup check; needs no credentials
./scripts/run_baseline.sh                        # pass 1 over tasks.txt

python3 retry_loop.py --task <failed-task> --arm control --max-retries 3
python3 retry_loop.py --task <failed-task> --arm deep    --max-retries 3
```

Both arms get the **same attempt budget**. `control` never sees advice;
`deep` re-diagnoses the trajectory that just failed before each retry and
carries earlier advice forward so the agent does not re-enter a dead end. Only
the difference between the arms is evidence about AgentDebugX — a `deep` result
on its own just measures having more than one try.

Defaults come from `.env`: `TB_MODEL`, `TB_EFFORT`, `TB_MAX_RETRIES`.
Claude-based arms use `claude_installer.yaml` by default; set
`CLAUDE_INSTALLER_CONFIG` to select another config.

## Reading the output

Harbor writes to `$HARBOR_JOBS_DIR/<job>/<task>__<trial>/`; Harbor's own ATIF
is `agent/trajectory.json`, and Claude's native JSONL is under
`agent/sessions/projects/-app/`. Reward is under `verifier/`. AgentDebugX's
*own*, differently-schemed trajectory files (built from the native JSONL, not
from Harbor's ATIF — see
[architecture.md's Terminology section](docs/architecture.md#terminology-two-unrelated-things-are-both-called-trajectoryjson))
stay in `$AGENTDEBUG_EVAL_DIR`, deliberately outside the job dirs, so
re-running a job never destroys a diagnosis.

Three failure classes, worth keeping distinct when reading results:

- **agent failure** — reward 0.0, no exception. The only kind the eval is about.
- **harness failure** — e.g. `RuntimeError: server died` (harbor's singularity
  bootstrap) or `RewardFileNotFoundError`. Exclude from agent results.
- **invalid task** — the oracle itself scores 0.0, so agent failure and
  environment failure are indistinguishable. Exclude.
