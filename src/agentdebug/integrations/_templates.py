"""Template strings for the generated Claude Code Skill bundle.

Each constant is the verbatim content of a file written into the skill
directory by ``write_skill_bundle``:

* ``SKILL_TEMPLATE``       → ``SKILL.md``           (formatted with ``{name}`` / ``{allowed_tools}``)
* ``REFERENCE_TEMPLATE``   → ``references/cli_reference.md``
* ``CAPABILITIES_TEMPLATE``→ ``references/capabilities.md``
"""

SKILL_TEMPLATE = """\
---
name: "{name}"
description: "Debug a failed LLM agent run, attribute blame to the step or event that caused a failure, classify a trajectory against a known error taxonomy, or generate a structured diagnostic report. Use when the user references agent trajectory JSON, SQLite store, or JSONL store path. IMPORTANT: never read trajectory files directly for reasoning — always pass them through the agentdebug CLI."
argument-hint: "debug this agent run, why did this agent fail, find the root cause, analyze the trajectories, diagnose this trace, what went wrong in this agent, explain this agent failure"
allowed-tools: {allowed_tools}
---

# AgentDebugX — Claude Code Skill

Drives the locally-installed `agentdebug` CLI to debug failed agent runs.

## Prerequisites

```bash
pip install agentdebugx            # core
pip install 'agentdebugx[ui]'      # for `agentdebug inspect`
export AGENTDEBUG_LLM_BASE_URL=... # for `diagnose --mode judge/deep`
export AGENTDEBUG_LLM_API_KEY=...
```

Run `agentdebug doctor` first to confirm which adapters are available.

## Ground rule

**Never read trajectory files directly.** Do not use Read, Glob, or manual JSON inspection — always pass them through the `agentdebug` CLI.

**For flags and all available tools, read `references/cli_reference.md` first.** Only fall back to `--help` for version-specific flags not in that file.

## Steps

Work down this list; escalate only when the result is inconclusive.

0. **Normalize (optional)** — skip unless the user asks to save a file, or step 1 fails to parse:
   ```bash
   agentdebug convert <export.json|jsonl> --format auto --out trajectory.json
   ```

1. **Quick scan** — offline, no LLM needed:
   ```bash
   agentdebug diagnose <trajectory.json|raw-export> --mode heuristic --attributor none --recovery none
   ```

2. **LLM judge** — when step 1 is inconclusive and `AGENTDEBUG_LLM_*` is set:
   ```bash
   agentdebug diagnose <trajectory.json|trace_id> --mode judge --attributor all-at-once --recovery none
   ```

3. **DeepDebug** — when judge waffles or the root cause looks multi-step:
   ```bash
   agentdebug diagnose <trajectory.json|trace_id> --mode deep --attributor none --recovery none --traceback
   ```

**Inspect a store:**
```bash
agentdebug list --store-sqlite .agentdebug/errors.sqlite
agentdebug show <trace_id> --store-sqlite .agentdebug/errors.sqlite
```

**Publish to Hub** (ask the user for permission first):
```bash
agentdebug hub push <trace_id> --to git:<remote>#bundles --store-sqlite .agentdebug/errors.sqlite
```

## Escalation policy

**Analysis** — always start cheap; escalate only when inconclusive:
1. `diagnose` — always first; offline, free, deterministic
2. `diagnose --mode judge` — when heuristics find nothing conclusive or confidence is low
3. `diagnose --mode deep` — when judge waffles, or root cause looks multi-step (upstream of flagged event)

**Attribution** — set `--attributor all-at-once`, `step-by-step`, `binary-search`, or `counterfactual`.

**Recovery** — set `--recovery reflexion`, `critic`, `self-refine`, `auto-manual`, or `saga-rollback`.

See [capabilities.md](references/capabilities.md) for what each level does.

## Reporting

Always present: root-cause hypothesis (event_id + step + failure mode + confidence), supporting evidence (quoted from the trajectory), and a concrete suggested fix.

**Hedging is mandatory:**
- Confidence `null` or < 0.9: use "candidate root cause", "likely", or "ranked hypothesis" — never flat assertions like "the root cause is".
- Confidence ≥ 0.9 AND corroborated by `deep`: may state directly.
- Section headings count — write `## Candidate Root Cause`, not `## Root Cause: X`.

## Safety

Recovery suggestions are propose-only and require explicit user opt-in before any application.

If the user asks to apply a recovery:
- **Interactive session**: confirm they understand opt-in is required, then proceed only on explicit approval.
- **Non-interactive session** (e.g. `claude -p`): state in your output that opt-in is required and halt — do not apply.
"""


REFERENCE_TEMPLATE = """\
# AgentDebugX CLI reference

Full command catalog for the `agentdebug` CLI that this skill wraps. Run
`agentdebug <command> --help` for the authoritative, version-specific flags.

## Mental model

AgentDebugX has four primary CLI stages:

| Stage | Primary command | Alias |
|-------|----------------|-------|
| Normalize external traces | `ingest` | `convert` |
| Diagnosis, attribution, recovery | `diagnose` | `analyze` |
| Local dashboard / store inspection | `inspect` | `serve` |
| Host integration generation | `integrations` | hidden legacy `act integrations` |
| Error Hub | `hub` | hidden legacy `act hub` |

`list` and `show` are standalone store commands with no alias.

## Environment

LLM-backed `diagnose` modes (`--mode judge`, `--mode deep`) and LLM-backed
attributors/recoveries read credentials from the environment
(or accept explicit overrides):

```bash
export AGENTDEBUG_LLM_BASE_URL=...   # OpenAI-compatible endpoint
export AGENTDEBUG_LLM_API_KEY=...
export AGENTDEBUG_LLM_MODEL=...      # optional; defaults to gemini-3-flash
```

Override flags: `--base-url URL`, `--api-key KEY`, `--model NAME`

Missing credentials cause LLM-backed diagnosis to exit with code 4.

## Store arguments

`list`, `show`, `diagnose`, `hub push`, and `inspect` accept a mutually
exclusive store selector:

```
--store-sqlite <path>    SQLite trace store
--store-jsonl  <path>    JSONL trace store
```

## Commands

### ingest / convert

Normalize an offline trace export into `AgentTrajectory` JSON.

```bash
agentdebug ingest <export.json|jsonl> [--out PATH] [--format FORMAT] \\
    [--trace-id ID] [--task-id ID] [--goal TEXT] [--framework NAME]
agentdebug convert ...   # identical alias
```

`--format` choices:

| Value | Meaning |
|-------|---------|
| `auto` *(default)* | Auto-detect |
| `agenttrajectory` | Native AgentDebugX trajectory |
| `messages` | OpenAI / chat-style messages payload |
| `message_list` | Flat list of chat messages |
| `conversations` | Rollout conversation format |
| `event_list` | Generic event list |
| `webshop_pages` | WebShop page/action logs |
| `openai_agents_spans` | OpenAI Agents span dumps |
| `crewai_events` | CrewAI event dumps |
| `langgraph_callbacks` | LangGraph / LangChain callback logs |
| `openclaw` | OpenClaw session or runtime trajectory JSONL |
| `claude_code` | Claude Code project/session JSONL |
| `hermes` | Hermes session export |

Exits 2 on conversion error.

### diagnose / analyze

Diagnosis, optional attribution, and optional recovery planning.

```bash
agentdebug diagnose <trajectory.json|external-trace> \\
    --mode heuristic|judge|deep|gui-rca \\
    --attributor none|heuristic|all-at-once|step-by-step|binary-search|counterfactual \\
    --recovery none|reflexion|critic|self-refine|auto-manual|saga-rollback \\
    [--out PATH] [--traceback] [--no-color] \\
    [--rule-pack auto|core|agenterrorbench|all]
agentdebug analyze ...   # identical alias
```

| Flag | Effect |
|------|--------|
| `--mode heuristic` | Deterministic local rules; no LLM required |
| `--mode judge` | LLM judge diagnosis |
| `--mode deep` | DeepDebug iterative diagnosis |
| `--attributor heuristic` | Deterministic blame localization |
| `--recovery reflexion` | Append Reflexion-style retry suggestions |
| `--recovery critic` | Suggest verifier/guard recovery |
| `--recovery self-refine` | LLM critic/refiner recovery |
| `--traceback` | Render a Python-traceback-style cascade instead of JSON |
| `--no-color` | Disable ANSI colors in `--traceback` output |
| `--rule-pack` | Repeatable. `auto` loads `core` + any detected benchmark pack |
| `--out PATH` | Write report to file instead of stdout |

`diagnose` also auto-converts recognized external formats inline (same as
running `ingest` first) — so you can point it directly at a raw export.

### list

List all trace IDs in a store.

```bash
agentdebug list --store-sqlite .agentdebug/errors.sqlite
agentdebug list --store-jsonl  .agentdebug/traces.jsonl
```

Exits 2 if no store is provided.

### show

Print one stored trajectory as JSON.

```bash
agentdebug show <trace_id> --store-sqlite .agentdebug/errors.sqlite
```

Exits 2 (no store) / 3 (unknown trace_id).

### inspect / serve

Run the local FastAPI dashboard. Requires `agentdebugx[ui]`.

```bash
agentdebug inspect --store-sqlite .agentdebug/errors.sqlite \\
    [--host 127.0.0.1] [--port 7777]
agentdebug serve ...   # identical alias
```

Exits 5 if the `[ui]` extra is not installed. Dashboard endpoints:
`/healthz`, `/overview`, `/trace/{trace_id}`, `/api/v1/traces`.

### LLM diagnose modes

LLM judge against a trajectory file or stored trace:

```bash
agentdebug diagnose <trajectory.json|trace_id> \\
    [--store-sqlite PATH | --store-jsonl PATH] \\
    --mode judge --attributor all-at-once --recovery critic \\
    [--model NAME] [--base-url URL] [--api-key KEY] \\
    [--traceback] [--no-color] [--out PATH]
```

Iterative DeepDebug for hard failures:

```bash
agentdebug diagnose <trajectory.json|trace_id> \\
    [--store-sqlite PATH | --store-jsonl PATH] \\
    --mode deep --attributor binary-search --recovery self-refine \\
    [--model NAME] [--base-url URL] [--api-key KEY] \\
    [--traceback] [--no-color] [--out PATH]
```

Exits 4 if LLM credentials are missing.

### act hub / hub

Package, publish, list, and pull Error Hub bundles.

**Hub specs** — `<spec>` is one of:
`local:/path`, `git:<remote>[#<path>]`, `hf:<repo_id>[#<path>]`

```bash
# push — scrubs PII/secrets by default
agentdebug hub push <trace_id> --to <spec> \\
    --store-sqlite PATH | --store-jsonl PATH \\
    [--no-scrub] [--license CC-BY-4.0] \\
    [--contributor ID] [--contributor-org ORG] [--message TEXT]

# pull
agentdebug hub pull <spec> --bundle <bundle_id> [--into .agentdebug/hub_pulls]

# list
agentdebug hub list <spec> [--limit 50]
```

`--no-scrub` skips PII/secret scrubbing — only for trusted internal hubs.
Exits 4 on push/pull/list failure (network, auth, unknown bundle).

### act integrations / integrations

Generate host-runtime integration files.

```bash
# Claude Code Skill
agentdebug integrations skill \\
    [--target ~/.claude/skills] [--name agentdebug]

# OpenHands microagent
agentdebug integrations openhands-microagent \\
    [--target .openhands/microagents] [--name agentdebug]
```

Both `agentdebug integrations ...` and `agentdebug act integrations ...` work.

### doctor

Report which framework adapters and integrations are available.

```bash
agentdebug doctor
```

Checks: `langgraph`, `crewai`, `openai-agents`, `otel`, `raw`. Also lists
registered plugins.

## Exit codes

| Code | Meaning |
|-----:|---------|
| `0` | Success |
| `1` | Unknown / unhandled command branch |
| `2` | Bad input, missing store, parse / convert / load failure |
| `3` | Unknown `trace_id` in a store |
| `4` | LLM or hub operation failure; often missing LLM credentials |
| `5` | UI dependency missing for `serve` / `inspect` |

## Optional extras

```bash
pip install 'agentdebugx[ui]'          # inspect / serve
pip install 'agentdebugx[langgraph]'   # LangGraph adapter
pip install 'agentdebugx[crewai]'      # CrewAI adapter
pip install 'agentdebugx[openai-agents]'
pip install 'agentdebugx[otel]'        # OpenTelemetry GenAI
pip install 'agentdebugx[hub-hf]'      # Hugging Face hub backend
pip install 'agentdebugx[all]'         # most extras at once
```

---

Generated by `agentdebug integrations skill` — re-run after
`pip install -U agentdebugx` to refresh this skill.
"""


CAPABILITIES_TEMPLATE = """\
# AgentDebugX — Capabilities guide

What each analysis, attribution, and recovery capability does, and when to use it.

## Analysis

**`diagnose` (offline, free)**
Rule-based pattern matching against a known failure taxonomy. Fast and
deterministic. Catches tool schema errors, repeated calls, step-count overruns,
and other heuristic patterns. Confidence scores reflect rule match strength, not
LLM reasoning. Always run first.

**`judge` (LLM, single-pass)**
A language model reads the full trajectory and produces a structured verdict:
failure mode, confidence, and supporting evidence. Use when `diagnose` returns
no findings or low confidence. Add `--attribute` to also identify which specific
step or event caused the failure.

**`deep` (LLM, iterative)**
Multi-turn analysis: plan -> hypothesize -> verify -> refine. Not just "more
reasoning" — a structured hypothesis-test loop. Use when `judge` waffles, or
when the root cause is likely upstream of the flagged event (multi-step causal
chain). Qualitatively different from `judge`, not a fallback.

## Attribution

Answers "which step or event caused the failure."

`--attribute` on `judge` runs a single-pass LLM attributor over the full trace.
Sufficient for most traces. For very long traces where blame is distributed
across many steps, note this limitation to the user — step-by-step attribution
is available via the Python API but not yet exposed as a CLI flag.

## Recovery

Answers "what should the agent do differently."

`--suggest` on `diagnose` produces Reflexion-style retry suggestions tied to
each finding. Always start here. Escalate only if the suggestion is vague or
does not map to a specific event in the trace.

**Safety rules:**
- Suggestions are proposals only — AgentDebugX never applies changes automatically
- Strategies that touch write-class tools require explicit user opt-in and a
  registered compensation; AgentDebugX refuses to retry them without one
- Every fix proposal includes rationale, source detector(s), and a confidence score
- If the user asks Claude to *apply* a recovery, confirm they understand these
  rules before proceeding
"""
