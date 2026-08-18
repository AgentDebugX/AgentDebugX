#!/usr/bin/env bash
# Shared setup for every run_<arm>.sh script: load .env, refuse the login
# node, make sure the job/sif dirs exist, and expose run_arm/run_resume so an
# arm script only has to state its own flag overrides. Not run directly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# The eval dir (tasks.txt, claude_installer.yaml, run_eval.py, ...), distinct
# from the caller's own script dir — arm scripts should use this, not $HERE.
EVAL_DIR="$ROOT/examples/terminal_bench_eval"

set -a
# shellcheck disable=SC1091
. "$ROOT/.env"
set +a

: "${HARBOR_JOBS_DIR:?set in .env}"
: "${HARBOR_SIF_CACHE_DIR:?set in .env}"

if [ -z "${SLURM_JOB_ID:-}" ]; then
  echo "refusing to run on the login node: get an allocation first, e.g." >&2
  echo "  srun --partition=yongjoo --time=04:00:00 --nodes=1 \\" >&2
  echo "       --cpus-per-task=16 --mem=64G --pty /bin/bash" >&2
  exit 1
fi

mkdir -p "$HARBOR_JOBS_DIR" "$HARBOR_SIF_CACHE_DIR"

# Launch one `run_eval.py run` + `collect` pass. The caller passes the
# arm-specific flags (--arm, --agent, --task/--tasks-file, ...); jobs-dir and
# sif-cache-dir are always the pinned ones from .env.
run_arm() {
  local job_dir
  job_dir="$(python3 "$EVAL_DIR/run_eval.py" run \
    "$@" \
    --jobs-dir "$HARBOR_JOBS_DIR" \
    --sif-cache-dir "$HARBOR_SIF_CACHE_DIR")"
  echo "job dir: $job_dir"
  python3 "$EVAL_DIR/run_eval.py" collect "$job_dir" --out "$job_dir/trials.jsonl"
  echo "per-trial records: $job_dir/trials.jsonl"
}

# Drive resume_experiment.py (Seed reuse-or-launch, classification, and
# rerun/rerun-deep recovery methods, over one task or many — there is no
# separate batch mode) with the same pinned dirs. The caller passes
# --task/--tasks-file/--tasks-config, --method, and optionally
# --seed-trial-dir/--out.
run_resume() {
  python3 "$EVAL_DIR/resume_experiment.py" \
    "$@" \
    --jobs-dir "$HARBOR_JOBS_DIR" \
    --sif-cache-dir "$HARBOR_SIF_CACHE_DIR" \
    --out-dir "${AGENTDEBUG_EVAL_DIR:-$HARBOR_JOBS_DIR/agentdebug-eval}"
}
