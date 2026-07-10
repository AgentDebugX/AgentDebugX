# AgentDebugX Skill Demo User Guide

This guide documents the current runnable demo. The order is:

1. Build or install AgentDebugX.
2. Generate the host-agent skill.
3. Obtain or choose trajectories.
4. Run the demo with Claude Code or Hermes.
5. Inspect expected outputs and known limitations.

## 1. Build AgentDebugX

From the repository root:

```bash
python -m pip install -e .
agentdebug doctor
```

If you do not want to install into the active environment, use the source-tree
entry point:

```bash
PYTHONPATH=src python -m agentdebug.cli doctor
```

Deterministic ingest and heuristic diagnosis do not need API credentials.
LLM-backed judge, DeepDebug, LLM attributors, and `self-refine` recovery need
an OpenAI-compatible endpoint:

```bash
export AGENTDEBUG_LLM_BASE_URL="https://<openai-compatible-host>/v1"
export AGENTDEBUG_LLM_API_KEY="<secret>"
export AGENTDEBUG_LLM_MODEL="<model>"
agentdebug config doctor
```

or persist the config:

```bash
agentdebug config set-llm \
  --base-url "$AGENTDEBUG_LLM_BASE_URL" \
  --api-key "$AGENTDEBUG_LLM_API_KEY" \
  --model "$AGENTDEBUG_LLM_MODEL"
```

Do not commit secrets or generated local config.

## 2. Generate The Skill

### Claude Code

For a project-local Claude Code skill:

```bash
agentdebug integrations skill \
  --platform claude \
  --target .claude/skills \
  --name agentdebug
```

From a source checkout:

```bash
PYTHONPATH=src python -m agentdebug.cli integrations skill \
  --platform claude \
  --target .claude/skills \
  --name agentdebug
```

This writes:

```text
.claude/skills/agentdebug/
```

Launch Claude Code from the same project directory:

```bash
claude
```

### Hermes Agent

Hermes does not automatically discover `./.hermes/skills` as a project-local
skill directory. The default visible skill directory is:

```text
~/.hermes/skills/
```

Install the Hermes skill there:

```bash
agentdebug integrations skill \
  --platform hermes \
  --target ~/.hermes/skills \
  --name agentdebug
```

From a source checkout:

```bash
PYTHONPATH=src python -m agentdebug.cli integrations skill \
  --platform hermes \
  --target ~/.hermes/skills \
  --name agentdebug
```

Launch Hermes with the skill preloaded:

```bash
hermes chat --skills agentdebug
```

or use a one-shot prompt:

```bash
hermes chat --skills agentdebug -q "<prompt>"
```

### OpenClaw

Install the OpenClaw skill into the default OpenClaw skill directory:

```bash
agentdebug integrations skill \
  --platform openclaw \
  --target ~/.openclaw/skills \
  --name agentdebug
```

From a source checkout:

```bash
PYTHONPATH=src python -m agentdebug.cli integrations skill \
  --platform openclaw \
  --target ~/.openclaw/skills \
  --name agentdebug
```

## 3. Obtain Trajectories

The demo starts from exported trajectories. AgentDebugX should not silently
scan private host state to find runs.

### Use Checked-In Hermes Trajectories

The current real Hermes examples are under:

```text
examples/debug_skills/trajectories/hermes/gaia/
```

Useful cases:

```text
i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl
how-many-times-was-a-twitter-x-post-cited-as-a-r__50f58759.jsonl
```

### Export New Hermes Trajectories

Use Hermes CLI:

```bash
hermes sessions list
hermes sessions export session.jsonl --session-id <session-id>
hermes sessions export backup.jsonl
```

Hermes exports are JSONL with one session object per line. Each session object
contains session metadata plus `messages`.

Current limitation: `agentdebug ingest --format hermes` handles one Hermes
session at a time. If a JSONL export contains multiple sessions, split or
select one line per conversion until batch splitting is implemented.

### Obtain OpenClaw Trajectories

OpenClaw writes session traces under the active agent's session directory:

```text
~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
~/.openclaw/agents/<agentId>/sessions/<sessionId>.trajectory.jsonl
```

The plain `.jsonl` file is the session transcript. The `.trajectory.jsonl`
file is the runtime trajectory log. Either can be passed to AgentDebugX with
`--format openclaw` or `--format auto`.

## 4. Run The Recorded CLI Demo

This smoke test does not launch a host agent. It verifies conversion and
deterministic traceback generation over the checked-in Hermes GAIA fixtures:

```bash
bash examples/debug_skills/scripts/run_recorded_demo.sh
```

Outputs are written under:

```text
examples/debug_skills/out/
  normalized/
  reports/
  logs/
```

Use this first when checking importer or diagnosis plumbing.

## 5. Run The Claude Code Skill Demo

Start Claude Code from the repo or a separate test project where the skill is
installed:

```bash
claude
```

Example Prompt:

```text
Can you use agentdebug to analyze this agent log to see what went wrong, and what are the potential fixes? the agent did not produce the expected answer: /u/yuchen85/agent-debug/AgentDebugX/examples/debug_skills/trajectories/hermes/gaia/how-many-times-was-a-twitter-x-post-cited-as-a-r__50f58759.jsonl.
```

Expected behavior:

- Claude loads the AgentDebugX skill.
- It runs `agentdebug ingest --format hermes`.
- It runs deterministic diagnosis first.
- If LLM credentials are configured, it runs judge or DeepDebug with an LLM
  attributor.
- It treats recovery as next-run guidance, not as an applied patch.

Example analysis from a Claude Code run on the Twitter/X citation trajectory:

```text
Diagnosis: Twitter/X Wikipedia citation-count trajectory

Outcome: The agent never emitted a FINAL ANSWER: line at all — it ended with a rambling "Summary of Findings" admitting "No stable quantitative answer was reached," hitting the tool-call iteration limit (20) before finishing.

Candidate root cause (judge mode, confidence 0.90)

- Step 6, evt_1c571fc7a5064689bd6f1899ee6a4859, mode action.invalid_action
- The agent's own generated Python script had an IndentationError: expected an indented block after 'if' statement on line 17 — a self-inflicted syntax bug in code it wrote to parse Wayback Machine snapshot data.
- This wasted a turn and forced re-writes of the fetch logic, kicking off a long trial-and-error chain.

Cascade of downstream failures

1. Step 24 (action.parameter_error, conf 0.85) — a broken regex matched literal JS source (t.cookie.match(/(?:^|;']) instead of actual t.co links, meaning any link-counting done from this point was corrupted data.
2. Step 31 (planning.inefficient_plan, conf 0.90) — the agent realized mid-run that the Portal:Current_events/2023_August_N day-subpages didn't exist yet as of June 30, 2023 (they're created the day before publication), invalidating its entire page-selection strategy for "last June 2023 version." This should have been checked before writing any fetch code.
3. Steps 54 & 57 (system.tool_execution_error, conf 0.90–0.95) — repeated HTTP 429 Too Many Requests from the Wikipedia API, with no backoff/retry logic.
4. Step 61 (system.llm_limit, conf 0.95) — hit the 20-iteration cap and was forced to summarize instead of computing the actual count.

Why the final answer is wrong/missing

The task asked for a per-day count across all of August 2023. The agent burned most of its iteration budget recovering from its own code bugs (indentation error, bad regex) and a wrong assumption about which Wikipedia page revision to fetch, then got rate-limited, and ran out of turns — so it gave up with a qualitative summary instead of a number.

Suggested fixes (critic recovery proposals, conf 0.85)

1. Validate generated code before executing it — add a syntax/lint check (or dry-run compile) on agent-authored scripts before running them in the sandbox, so indentation/syntax errors are caught and retried without burning a full tool-call cycle.
2. Add a schema/type guard on tool results — the regex-vs-JS-source mismatch at step 24 is exactly the kind of "tool output the agent didn't validate" bug that a tool_result_typecheck (reject non-URL strings, assert result shape) would have caught immediately.
3. Verify page existence/versioning assumptions early — before writing fetch code for 31 day-subpages, first confirm (via a single lightweight API call) which pages actually existed as of the target snapshot date, avoiding steps 1–30 of wasted exploration.
4. Add retry/backoff for HTTP 429s — the agent had no rate-limit handling, so repeated bursts against the Wikipedia API cost several iterations for no data.
5. Raise the iteration budget or add mid-run progress checkpoints — with 20 max iterations and this many recoverable errors, the agent was structurally unlikely to finish; either increase the budget for multi-day-loop tasks like this or have it checkpoint partial per-day counts as it goes so a timeout still yields partial credit instead of zero answer.

These are next-run guidance, not applied changes — let me know if you'd like me to implement any of them (e.g., wire in the syntax-check-before-exec step) or dig into a specific cascade step further.
```

This is a good demo because it shows a cascade, not just one failed command.

## 6. Run The Hermes Self-Debug Demo

Start Hermes with the AgentDebugX skill:

```bash
hermes chat --skills agentdebug
```

Prompt:

```text
Use AgentDebugX to debug this failed Hermes trajectory:

/u/yuchen85/agent-debug/AgentDebugX/examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl

Tell me the likely root cause, cite the evidence, and explain what should
change for the next run.
```

Example analysis from a Hermes self-debug run:

```text
Based on the AgentDebugX diagnosis, here is the likely root cause and what should change next run:

Likely root cause

Candidate root cause is step 14, event evt_9e7e20606c494e1cb026b3f09371466f:
- Failure mode: system.tool_execution_error
- Agent tried to shell out to pdftotext, but the binary is not installed on the
  host. Evidence: /usr/bin/bash: line 3: pdftotext: command not found

Manifested failure

- Instead of recovering with a valid PDF extraction path, the agent drifted into
  repeated web search / web extract attempts (planning loops at steps 44, 47,
  50, 64). The run eventually hit max_steps=50 / 65 events and terminated
  without producing a final answer.

What should change next run

1. Remove the environment assumption that pdftotext exists.
   - Prefer a Python-based fallback such as pdfplumber, pymupdf, or
     pdftotext via a python wrapper that can degrade gracefully.
   - Branch: if the CLI pdftotext check fails, immediately try a Python PDF
     library instead of abandoning PDF extraction entirely for web search.

2. Add loop/retry classification on tool failure.
   - After step 14's tool error, the agent should classify it as retryable
     versus terminal. A missing PDF CLI is retryable via fallback extractor;
     the current behavior cascaded into redundant planning loops.

3. Enforce loop detection.
   - The trajectory shows repeated web_search / web_extract state in a
     short window. Limit repeated identical tool-call signatures within a
     window and force a plan revision rather than cycling.

4. Cap search depth on site-local retrieval.
   - Once a promising arXiv/paper candidate is found, spend steps on extracting
     the specific PDF/figure rather than broadening web search.

Rough fix priority

- Immediate: replace the pdftotext call with a Python PDF extraction path
  that does not require external binaries.
- Follow-up: add loop guargs around web_search/web_extract so the agent
  doesn't exhaust max_steps when retrieval is noisy.
```

This is useful because it tests Hermes using a skill to diagnose a Hermes
trajectory, which is the product behavior we want first.

## 7. Optional: Skill Test Packets

`scripts/prepare_skill_test_packet.sh` creates a controlled packet with a
prompt, expected artifact paths, a skill snapshot, and a Codex review rubric.
This is mainly useful for internal skill-development review. It is not the
main user-facing demo.

```bash
bash examples/debug_skills/scripts/prepare_skill_test_packet.sh
```

Use this only when you want to inspect whether a host agent followed the skill
workflow exactly.

## 8. What To Look For

A good demo run should produce:

- a normalized `AgentTrajectory`,
- a deterministic traceback or report,
- an LLM-backed report when credentials are configured,
- a final explanation with event IDs or step indices,
- recovery guidance framed as next-run advice.

Known limitations:

- Current reports often catch visible symptoms before deeper planning causes.
- Hermes JSONL does not fully encode the agent's live tool and skill affordances.
- Recovery has not yet been shown as a fully automated retry-and-verify loop.
- Multi-session Hermes JSONL needs manual splitting today.
