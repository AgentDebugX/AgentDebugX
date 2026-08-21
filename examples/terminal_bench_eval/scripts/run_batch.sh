#!/usr/bin/env bash
# Seed -> rerun-deep across many tasks in one call — the same tool as
# run_seed.sh/run_rerun_deep.sh, just given more than one task. Defaults to
# every Oracle-qualified task in tasks.txt. Do not run this default until the
# candidate list has been pruned using the latest Oracle results.
#   ./run_batch.sh                              # every qualified task in tasks.txt
#   TB_TASKS="raman-fitting cancel-async-tasks" ./run_batch.sh   # a subset, all fresh seeds
#   TB_TASKS_CONFIG=tasks_with_reuse.yaml ./run_batch.sh   # YAML: mix fresh + reused seeds
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/run_base.sh"

RUN_ARGS=(
  --model "${TB_MODEL:-anthropic/claude-sonnet-5}"
  --effort "${TB_EFFORT:-medium}"
  --claude-installer-config "${CLAUDE_INSTALLER_CONFIG:-$EVAL_DIR/claude_installer.yaml}"
)

if [ -n "${TB_TASKS_CONFIG:-}" ]; then
  RUN_ARGS+=(--tasks-config "$TB_TASKS_CONFIG")
elif [ -n "${TB_TASKS:-}" ]; then
  for task in $TB_TASKS; do
    RUN_ARGS+=(--task "$task")
  done
else
  RUN_ARGS+=(--tasks-file "$EVAL_DIR/tasks.txt")
fi

run_resume "${RUN_ARGS[@]}"
