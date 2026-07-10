#!/usr/bin/env bash
# Run AgentDebugX Claude Code skill integration scenarios.
#
# Usage:
#   ./run.sh                     # run all scenarios (skip LLM ones if no creds)
#   ./run.sh --scenario <id>     # run one scenario
#   ./run.sh --no-regen          # re-check committed artifacts only (no claude call)
#   ./run.sh --model <alias>     # override claude model (default: sonnet)
#
# Env vars:
#   AGENTDEBUG_LLM_API_KEY / _BASE_URL / _MODEL   required for scenario 03
#
# Results are written to examples/claude_skill_integration/artifacts/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCENARIOS_FILE="$SCRIPT_DIR/scenarios.json"
ARTIFACTS_DIR="$SCRIPT_DIR/artifacts"
CHECK_PY="$SCRIPT_DIR/check.py"

ONLY_SCENARIO=""
NO_REGEN=0
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenario) ONLY_SCENARIO="$2"; shift 2 ;;
        --no-regen) NO_REGEN=1; shift ;;
        --model)    CLAUDE_MODEL="$2"; shift 2 ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$ARTIFACTS_DIR"

_has_llm_creds() {
    [[ -n "${AGENTDEBUG_LLM_API_KEY:-}" && -n "${AGENTDEBUG_LLM_BASE_URL:-}" ]]
}

# Read scenario list from JSON
SCENARIO_IDS=($(python3 -c "
import json, sys
scenarios = json.load(open('$SCENARIOS_FILE'))
for s in scenarios:
    print(s['id'])
"))

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# ------------------------------------------------------------------
# Setup: deploy generated skill into a temp project dir (once)
# ------------------------------------------------------------------
TMP_PROJECT=""
if [[ $NO_REGEN -eq 0 ]]; then
    TMP_PROJECT="$(mktemp -d)"
    trap 'rm -rf "$TMP_PROJECT"' EXIT

    echo "[setup] deploying generated skill → $TMP_PROJECT/.claude/skills/"
    agentdebug integrations skill --target "$TMP_PROJECT/.claude/skills" --name agentdebug

    # Verify real ~/.claude/skills is untouched (no agentdebug entry we didn't expect)
    echo "[setup] skill deployed; real ~/.claude/skills untouched ✓"
fi

# ------------------------------------------------------------------
# Run each scenario
# ------------------------------------------------------------------
for SCENARIO_ID in "${SCENARIO_IDS[@]}"; do
    [[ -n "$ONLY_SCENARIO" && "$SCENARIO_ID" != "$ONLY_SCENARIO" ]] && continue

    STREAM_FILE="$ARTIFACTS_DIR/${SCENARIO_ID}.stream.json"
    SUMMARY_FILE="$ARTIFACTS_DIR/${SCENARIO_ID}.summary.md"

    # Parse scenario metadata
    SCENARIO_JSON="$(python3 -c "
import json
s = next(x for x in json.load(open('$SCENARIOS_FILE')) if x['id'] == '$SCENARIO_ID')
print(json.dumps(s))
")"
    REQUIRES_LLM="$(echo "$SCENARIO_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('requires_llm', False))")"
    PROMPT="$(echo "$SCENARIO_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['prompt'])")"

    # Skip LLM scenario if no creds
    if [[ "$REQUIRES_LLM" == "True" ]] && ! _has_llm_creds; then
        echo "SKIP  $SCENARIO_ID  (no AGENTDEBUG_LLM_* credentials set)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
    fi

    # --no-regen: evaluate existing artifact
    if [[ $NO_REGEN -eq 1 ]]; then
        if [[ ! -f "$STREAM_FILE" ]]; then
            echo "SKIP  $SCENARIO_ID  (no committed artifact at artifacts/${SCENARIO_ID}.stream.json)"
            SKIP_COUNT=$((SKIP_COUNT + 1))
            continue
        fi
        echo "[check] $SCENARIO_ID (committed artifact)"
        if python3 "$CHECK_PY" "$SCENARIOS_FILE" "$SCENARIO_ID" "$STREAM_FILE" \
                --out "$SUMMARY_FILE"; then
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
        continue
    fi

    # ------------------------------------------------------------------
    # Prepare fixture in temp project dir
    # ------------------------------------------------------------------
    FIXTURE_SOURCE="$(echo "$SCENARIO_JSON" | python3 -c "
import json, sys
s = json.load(sys.stdin)
f = s.get('fixture', {})
print(f.get('source', '') if isinstance(f, dict) else f)
")"
    FIXTURE_DEST="$(echo "$SCENARIO_JSON" | python3 -c "
import json, sys
s = json.load(sys.stdin)
f = s.get('fixture', {})
print(f.get('dest', 'trace.json') if isinstance(f, dict) else 'trace.json')
")"
    JSONL_ID="$(echo "$SCENARIO_JSON" | python3 -c "
import json, sys
s = json.load(sys.stdin)
f = s.get('fixture', {})
print(f.get('jsonl_id', '') if isinstance(f, dict) else '')
")"

    # Extract full_trajectory from JSONL if needed
    if [[ -n "$JSONL_ID" ]]; then
        echo "[setup] extracting $JSONL_ID from $FIXTURE_SOURCE"
        python3 -c "
import json, pathlib, sys
rows = [json.loads(l) for l in open('$REPO_ROOT/$FIXTURE_SOURCE')]
row = next(r for r in rows if r['trajectory_id'] == '$JSONL_ID')
pathlib.Path('$TMP_PROJECT/$FIXTURE_DEST').write_text(row['full_trajectory'])
"
    else
        cp "$REPO_ROOT/$FIXTURE_SOURCE" "$TMP_PROJECT/$FIXTURE_DEST"
    fi

    # ------------------------------------------------------------------
    # Run claude
    # ------------------------------------------------------------------
    echo "[run]  $SCENARIO_ID → calling claude -p ..."

    ALLOWED_TOOLS="Bash(agentdebug *)"
    if [[ "$REQUIRES_LLM" == "True" ]]; then
        ALLOWED_TOOLS="Bash(agentdebug *)"
    fi

    (
        cd "$TMP_PROJECT"
        claude -p "$PROMPT" \
            --output-format stream-json \
            --verbose \
            --model "$CLAUDE_MODEL" \
            --allowedTools "$ALLOWED_TOOLS" Read Glob Grep Skill \
            --max-turns 15 \
            2>/dev/null || true
    ) | tee "$STREAM_FILE"

    # Reformat JSONL → pretty-printed JSON array for readability
    python3 -c "
import json, pathlib
p = pathlib.Path('$STREAM_FILE')
lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
p.write_text(json.dumps(lines, indent=2))
"

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------
    echo "[check] $SCENARIO_ID"
    if python3 "$CHECK_PY" "$SCENARIOS_FILE" "$SCENARIO_ID" "$STREAM_FILE" \
            --out "$SUMMARY_FILE"; then
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

# ------------------------------------------------------------------
# Summary table
# ------------------------------------------------------------------
echo ""
echo "────────────────────────────────────"
echo "  PASS: $PASS_COUNT   FAIL: $FAIL_COUNT   SKIP: $SKIP_COUNT"
echo "────────────────────────────────────"

[[ $FAIL_COUNT -eq 0 ]]
