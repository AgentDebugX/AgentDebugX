#!/usr/bin/env bash
# A2: same second attempt as run_retry_control.sh, plus the AgentDebugX
# deep-debug advice up front.
#   TB_TASK=<failed-task> TB_ADVICE=<advice.md from 'run_eval.py diagnose'> \
#     ./run_retry_deep.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

: "${TB_TASK:?set TB_TASK to the failed task name}"
: "${TB_ADVICE:?set TB_ADVICE to the advice markdown produced by 'run_eval.py diagnose'}"

RUN_ARGS=(
  --arm retry-deep
  --task "$TB_TASK"
  --agent claude-code
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
  --n-concurrent "${TB_CONCURRENCY:-4}"
  --extra-instruction-path "$TB_ADVICE"
)

run_arm "${RUN_ARGS[@]}"
