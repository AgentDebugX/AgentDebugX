#!/usr/bin/env bash
# Oracle pass over tasks.txt: setup check, no credentials needed.
#   ./run_oracle.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

RUN_ARGS=(
  --arm oracle
  --tasks-file "$EVAL_DIR/tasks.txt"
  --agent oracle
  --n-concurrent "${TB_CONCURRENCY:-4}"
)

run_arm "${RUN_ARGS[@]}"
