#!/usr/bin/env bash
# rerun: prior Seed conversation + a neutral verifier-failure notice, no
# diagnosis. Measures retry headroom with retained context.
#
# Reuses TB_SEED_TRIAL_DIR if set; otherwise launches Seed fresh for TB_TASK.
#   TB_TASK=terminal-bench/sqlite-db-truncate ./run_rerun.sh
#   TB_SEED_TRIAL_DIR=<jobs-dir job>/<task>__<trial> \
#   TB_TASK=terminal-bench/sqlite-db-truncate \
#     ./run_rerun.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

: "${TB_TASK:?set TB_TASK, e.g. terminal-bench/sqlite-db-truncate}"

RUN_ARGS=(
  --task "$TB_TASK"
  --method rerun
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
)
if [ -n "${TB_SEED_TRIAL_DIR:-}" ]; then
  RUN_ARGS+=(--seed-trial-dir "$TB_SEED_TRIAL_DIR")
fi

run_resume "${RUN_ARGS[@]}"
