#!/usr/bin/env bash
# Shared setup for the evaluation entry scripts: load .env, validate the chosen
# runtime, make the required output/cache dirs, and expose run_method/run_resume.
# Not run directly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# The eval dir (tasks.txt, claude_installer.yaml, run_eval.py, ...), distinct
# from the caller's own script dir — entry scripts should use this, not $HERE.
EVAL_DIR="$ROOT/examples/terminal_bench_eval"

set -a
# shellcheck disable=SC1091
. "$ROOT/.env"
set +a

: "${HARBOR_JOBS_DIR:?set in .env}"
TB_ENVIRONMENT="${TB_ENVIRONMENT:-singularity}"

BACKEND_ARGS=(--environment-backend "$TB_ENVIRONMENT")
case "$TB_ENVIRONMENT" in
  singularity)
    : "${HARBOR_SIF_CACHE_DIR:?set in .env for the singularity backend}"
    if [ -z "${SLURM_JOB_ID:-}" ]; then
      echo "refusing a singularity run outside a SLURM allocation" >&2
      echo "  srun --partition=yongjoo --time=04:00:00 --nodes=1 \\" >&2
      echo "       --cpus-per-task=16 --mem=64G --pty /bin/bash" >&2
      exit 1
    fi
    mkdir -p "$HARBOR_SIF_CACHE_DIR"
    BACKEND_ARGS+=(--sif-cache-dir "$HARBOR_SIF_CACHE_DIR")
    ;;
  docker)
    docker info >/dev/null
    docker compose version >/dev/null
    ;;
  *)
    echo "TB_ENVIRONMENT must be docker or singularity, got: $TB_ENVIRONMENT" >&2
    exit 2
    ;;
esac

mkdir -p "$HARBOR_JOBS_DIR"

# Launch one `run_eval.py run` + `collect` pass. The caller passes the
# method-specific flags (--method, --agent, --task/--tasks-file, ...); jobs-dir and
# runtime-specific arguments are always derived above.
run_method() {
  local job_dir
  job_dir="$(python3 "$EVAL_DIR/run_eval.py" run \
    "$@" \
    --jobs-dir "$HARBOR_JOBS_DIR" \
    "${BACKEND_ARGS[@]}")"
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
    "${BACKEND_ARGS[@]}" \
    --out-dir "${AGENTDEBUG_EVAL_DIR:-$HARBOR_JOBS_DIR/agentdebug-eval}"
}
