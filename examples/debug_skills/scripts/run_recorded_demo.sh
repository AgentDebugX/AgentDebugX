#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TRAJ_DIR="$ROOT/examples/debug_skills/trajectories/hermes/gaia"
OUT_DIR="$ROOT/examples/debug_skills/out"
NORM_DIR="$OUT_DIR/normalized"
REPORT_DIR="$OUT_DIR/reports"
LOG_DIR="$OUT_DIR/logs"

mkdir -p "$NORM_DIR" "$REPORT_DIR" "$LOG_DIR"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

run_case() {
  local name="$1"
  local input="$2"
  local normalized="$NORM_DIR/${name}.trajectory.json"
  local report="$REPORT_DIR/${name}.traceback.txt"
  local ingest_stdout="$LOG_DIR/${name}.ingest.stdout.log"
  local ingest_stderr="$LOG_DIR/${name}.ingest.stderr.log"
  local diagnose_stdout="$LOG_DIR/${name}.diagnose.stdout.log"
  local diagnose_stderr="$LOG_DIR/${name}.diagnose.stderr.log"

  python -m agentdebug.cli ingest "$input" --format hermes --out "$normalized" \
    >"$ingest_stdout" \
    2>"$ingest_stderr"

  python -m agentdebug.cli diagnose "$normalized" \
    --mode heuristic \
    --attributor none \
    --recovery none \
    --traceback \
    --no-color \
    >"$report" \
    2>"$diagnose_stderr"
  cp "$report" "$diagnose_stdout"
  printf "%-18s %-42s %s\n" "$name" "$(basename "$input")" "$report"
}

printf "%-18s %-42s %s\n" "case" "input" "traceback"
printf "%-18s %-42s %s\n" "----" "-----" "---------"
for trajectory in "$TRAJ_DIR"/*.jsonl; do
  run_case "$(basename "$trajectory" .jsonl)" "$trajectory"
done

echo
echo "Wrote demo outputs to $OUT_DIR"
