#!/usr/bin/env bash
# claude-code, pass 1 over tasks.txt.
#   ./run_baseline.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

RUN_ARGS=(
  --arm baseline
  --tasks-file "$EVAL_DIR/tasks.txt"
  --agent claude-code
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
  --n-concurrent "${TB_CONCURRENCY:-4}"
)

run_arm "${RUN_ARGS[@]}"
