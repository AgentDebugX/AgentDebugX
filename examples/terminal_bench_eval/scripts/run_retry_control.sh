#!/usr/bin/env bash
# A0: plain second attempt on a failed task, no advice. The control that
# makes the retry-deep number mean something rather than just "two tries
# beat one".
#   TB_TASK=<failed-task> ./run_retry_control.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

: "${TB_TASK:?set TB_TASK to the failed task name}"

RUN_ARGS=(
  --arm retry-control
  --task "$TB_TASK"
  --agent claude-code
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
  --n-concurrent "${TB_CONCURRENCY:-4}"
)

run_arm "${RUN_ARGS[@]}"
