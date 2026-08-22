#!/usr/bin/env bash
# Oracle pass over a task file: setup check, no credentials needed.
#   ./run_oracle.sh
#   TB_TASKS_FILE=tasks_docker_oracle_5.txt TB_ENVIRONMENT=docker ./run_oracle.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

RUN_ARGS=(
  --method oracle
  --tasks-file "${TB_TASKS_FILE:-$EVAL_DIR/tasks.txt}"
  --agent oracle
  --n-concurrent "${TB_CONCURRENCY:-4}"
)

run_method "${RUN_ARGS[@]}"
