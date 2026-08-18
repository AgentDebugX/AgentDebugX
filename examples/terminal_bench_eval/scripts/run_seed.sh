#!/usr/bin/env bash
# Seed: one-shot claude-code attempt at a single task, the baseline the
# rerun/rerun-deep recovery methods branch from. Launches and classifies
# Seed only — no recovery method. See docs/EXPERIMENT_PROTOCOL.md.
#   TB_TASK=terminal-bench/sqlite-db-truncate ./run_seed.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

: "${TB_TASK:?set TB_TASK, e.g. terminal-bench/sqlite-db-truncate}"

RUN_ARGS=(
  --task "$TB_TASK"
  --method seed
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
)

run_resume "${RUN_ARGS[@]}"
