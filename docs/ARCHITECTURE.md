# DeepDebug: Unified Design (v1.0, 2026-07-08)

**Single source of truth** for what DeepDebug is, what is implemented today,
what is missing, and the acceptance rules for changing it. Supersedes the
scattered descriptions in module docstrings; the demo paper's Section 4 is the
prose rendering of this document.

---

## 1. Positioning: the Deep Error Analysis stage of the Diagnostic Core

DeepDebug is the **"Deep Error Analysis" box** in the AgentDebugX Diagnostic
Core (Figure 1, layer 2). It sits inside — not beside — the layer-2 flow:

```
Trace Capture → Normalized Schema → Error Taxonomy (+Auto Induction)
   → Detectors → LLM Judge → [ DEEP ERROR ANALYSIS ] → Who&When Attribution
   → Recovery Suggestions
```

Contract with its neighbors:

| Upstream input | How DeepDebug consumes it | Status |
|---|---|---|
| Normalized `AgentTrajectory` | the object it debugs | ✅ shipped |
| Taxonomy (+ induced modes) | `candidate_labels` / `label_hint` constrain its verdict vocabulary | ✅ plumbed (`DeepDebugAnalyzer(label_hint=…, candidate_labels=…)`) |
| Detector / judge findings | structured hints seeded into the agent's context | 🟡 partial (labels only; findings not injected) — **G5** |
| Deep memory (past cases) | top-k retrieval into context; writeback after each run | ✅ shipped, opt-in (`use_memory=True`) |
| Error Hub bundles | retrieval as prior cases / lessons | ❌ missing — **G2** |

| Downstream output | Consumer | Status |
|---|---|---|
| `DiagnosticReport` (root step/agent, taxonomy label, evidence, summary, **fix suggestion**) | Who&When attribution scoring; console; CLI | ✅ |
| `DeepDebugRecovery` retry directive (id `deepdebug`) | GAIA rerun loop; console rerun-from-event | ✅ |
| Audit trail (`DeepDebugRound`s) | console; regression fixtures | ✅ |

## 2. The harness contract

DeepDebug is **one diagnostic agent harness**: a multi-turn agent equipped with
tools over (a) the captured trace and (b) the environment the trace came from,
plus knowledge sources, with a bounded turn budget and a structured verdict.

Five clauses every channel must satisfy:

1. **Multi-turn.** The agent takes multiple investigative turns; a turn may
   invoke a tool or commit to a verdict. Turn cap enforced (text: 4 macro-turns
   + conditional arbitration; GUI: `MAX_TURNS` ReAct loop).
2. **Tool-using.** Tools are declared per channel from one registry (§4); the
   agent chooses among them (GUI channel) or the harness sequences them in the
   measured order (text channel, §3).
3. **Env-diving.** When the trace links to an on-disk / remote environment
   (OSWorld trajectory dir, GAIA attachments), the agent may open it —
   screenshots, files, task assets — not just re-read trace text.
4. **Knowledge-augmented.** Deep memory (retrieve + writeback), lessons, and
   (planned) Error Hub cases are injected as context, never as ground truth.
5. **Auditable, typed verdict.** Every turn is recorded; the final output is a
   `DiagnosticReport` whose first finding is the root cause with taxonomy
   label, quoted evidence, and one actionable fix suggestion.

**Never re-executes the debugged agent's own tools.** Env dive is read-only
inspection; replay/counterfactual re-execution stays a separate, future
attributor.

## 3. Text-trace channel: the measured macro-turn pipeline (default)

The default text channel runs a **fixed, measured** sequence (chosen by
ablation — free-form exploration is *not* the default because the fixed order
wins on Who&When; see §7):

| Turn | Name | What it does | Tool used |
|---|---|---|---|
| 1 | **Global read** | one pass over the whole trajectory; names a candidate decisive step in context | `read_full_trace` |
| 2 | **Structure-guided investigation** | multi-agent trace → walk the handoff cascade upstream from the visible failure; single-agent trace → bisect the step range, re-read the surviving span | `walk_cascade_upstream` / `bisect_range` + `read_span` |
| 3 | **Cross-examination** *(conditional)* | if Turns 1–2 agree → accept; else zoom both candidates ±k context windows and adjudicate | `zoom_step_context` |
| 4 | **Diagnose & suggest** | step now fixed; write summary + quoted evidence + **one concrete fix**; verdict cannot move the step | — |

Implementation: `src/agentdebug/diagnose/profiles/deepdebug.py`
(`DeepDebugAnalyzer`) over `src/agentdebug/diagnose/attribute/moe.py`
(`aao_moe_attribute`: `all_at_once` +
`_cascade`/`_bisect_refine` + arbitration). Memory retrieval (step 0) feeds
both readings and the final diagnosis turn. `DeepDebugResult.rounds` exposes
the four table stages directly. `AaoMoeAnalysis` preserves both candidates,
the cascade/bisection decisions and final window, and the adjudication verdict.
Every candidate carries `event_id + step_index + agent_name`; a bare step is
accepted only when unique. Final evidence is represented as an event-id quote,
verified against trajectory input/output/error text before entering the report.
Unverified model quotes are rejected and counted in report metadata.

DeepDebug lives under `diagnose/profiles/` because it orchestrates a complete
Diagnose workflow rather than implementing attribution alone. The former
`diagnose/attribute/deepdebug.py` module remains a compatibility re-export.
The registry ID `attribute.deepdebug` is also retained for existing plugin
configuration, but its entrypoint resolves to the canonical profile module.

**Design rationale (measured, not taste):** replacing the structure-guided
turn with a second global search costs −4.8 strict points (gpt-5.4-mini
ablation); the dual design wins every Who&When metric on qwen3.5-9b and the
strict metric on qwen3.6-27b; at the frontier a single reading suffices, so
the extra turns are opt-in budget, not a tax (docs/benchmarks/).

## 4. Tool registry (unified across channels)

One registry, per-channel exposure. GUI names exist today in
`cua_debugger/debugger/tools/`; text-channel "tools" are currently internal
functions of `moe.py` — same capabilities, not yet declared as callable tools
(**G4** promotes them for the opt-in explore mode).

| Tool | Purpose | Text channel | GUI/env channel |
|---|---|---|---|
| `read_full_trace` | whole-trajectory rendering (truncation-aware) | ✅ internal (`_render_marked`) | ✅ injected into initial prompt |
| `get_step_details` / `read_span` | one step ± context, full fidelity | ✅ internal (`_render_span`) | ✅ tool (returns action code, reasoning, tool use, **screenshots**) |
| `walk_cascade_upstream` | follow handoffs from failure toward origin | ✅ internal (`_cascade`), decisions audited | — (single-agent GUI runs) |
| `bisect_range` | divide-and-conquer step-range narrowing | ✅ internal (`_bisect_refine`), decisions audited | — |
| `zoom_step_context` | side-by-side candidate windows (arbitration) | ✅ internal | ✅ via `get_step_details` |
| `open_env_asset` | dive into env: screenshots, task files, attachments | ❌ **G3** (GAIA attachments) | ✅ (OSWorld dir via `IngestionResult.from_directory`; old screenshots auto-compressed) |
| `search_memory` | top-k similar past cases | ✅ step-0 retrieval (`use_memory`) | 🟡 lessons variant |
| `lookup_lessons_by_taxonomy` / `search_lessons_by_app` / `follow_episodic_ref` | curated lesson base | ❌ text | ✅ tools (`rca_with_lessons` context) |
| `search_error_hub` | prior community/team cases from Error Hub bundles | ❌ **G2** | ❌ **G2** |
| `finish` | commit structured verdict | ✅ Turn 4 | ✅ tool (schema-enforced) |

## 5. GUI / env channel (CUA · OSWorld)

`GuiRcaAnalyzer` (`src/agentdebug/diagnose/gui_rca.py`) satisfies the harness
contract with a **free ReAct loop** (`cua_debugger/debugger/agent.py:
run_react_loop`, turn-capped, old screenshots compressed to bound context):

- **Ingest/env dive**: `IngestionResult.from_directory(osworld_root)` — the
  agent inspects the actual trajectory directory (screenshots + step files),
  resolved from `metadata['source_dir']` or screenshot artifact URIs.
- **Tools**: `get_step_details` (text + images), lesson tools, `finish`.
- **Model routing**: `runtime/llm_channel.py` presents an Anthropic-style
  `.messages.create` seam but executes through our `OpenAICompatClient` — one
  LLM stack for both channels, vision included.
- **Verdict mapping**: `RCAResult` → `DiagnosticReport` with the GUI taxonomy
  (`runtime/gui_taxonomy.py`); infeasible-task branch handled at ingestion.
- CLI: `agentdebug diagnose --mode gui-rca --rule-pack gui` (+ `--format
  osworld` ingest).

## 6. Knowledge: memory, lessons, Error Hub

| Source | Mechanism | Status |
|---|---|---|
| **Deep memory** | `SQLiteDeepMemoryStore.search_references` (lexical, embedding-cosine when configured) before analysis; `save_run` after; `NullMemoryStore` default = zero side effects | ✅ shipped, opt-in |
| **Lessons** (GUI) | lesson explorer tools over curated lesson base; `rca_with_lessons` context | ✅ shipped (GUI channel) |
| **Error Hub** | bundles (trajectory+report+artifacts) as a retrieval corpus: hub → memory import so accepted team/community cases seed future diagnoses | ❌ **G2** — design: `hub pull` → `DeepMemoryStore.save_run` per bundle; no new schema needed |
| **AutoManual rules** | distilled one-line rules re-injected into future runs | ✅ recoverer exists; not auto-fed into DeepDebug context (fold into G2/G5) |

## 7. Evaluation contract & current evidence

| Benchmark | What it tests | Current DeepDebug result | SOTA bar |
|---|---|---|---|
| Who&When (n=184) | agent + step localization | **wins every metric** on qwen3.5-9b (56.0 agent / 28.8 strict vs 47.8 / 21.7 best single); strict 38.0 on qwen3.6-27b; margin concentrates on >40-event traces (0%→8% strict) | ✅ current SOTA among our backends; beats AgentDebug-paper protocol baselines |
| AgentErrorBench | critical-step on failed ALFWorld/GAIA/WebShop | best exact-step on qwen3.5-9b (0.170) and gemini-3.5-flash (0.190); concedes qwen3.6-27b (0.223 baseline) | 🟡 close the 27B cell in the rerun |
| GAIA recovery (n=165) | diagnosis → fix → rerun | 30/61 failures recovered via CRITIC-over-shared-diagnosis → 81.2% | redesigned rerun: DeepDebug-direct row must ≥ 30/61 (acceptance rule below) |
| OSWorld (GUI) | root error step on CUA runs | vendored channel; numbers pending public data drop | establish baseline in rerun |

**Acceptance rule (unchanged):** any redesign must not regress the completed
round on any reported cell; if it does, analyze, iterate, or revert — never
ship a worse number for narrative reasons. Every design change lands with a
measured before/after (the judge-seeding → dual-reading history is the
precedent: both replacements were adopted only after beating the incumbent).

## 8. Gap plan (what to build, in order)

| Gap | Work | Size | Acceptance |
|---|---|---|---|
| **G2** Error Hub → memory | `agentdebug act hub pull` imports bundles into `DeepMemoryStore`; DeepDebug `use_memory` then sees team/community cases | S | unit test: hub bundle retrievable as `MemoryReference`; no regression w/ memory off |
| **G5** Findings injection | pass detector/judge findings (not just labels) into both readings' context as "prior signals, may be wrong" | S | Who&When A/B: ≥ no-change on strict; keep if +Δ |
| **G3** Text env dive | `open_env_asset` tool: resolve GAIA attachments / task files from trajectory artifacts; expose in refine turn | M | GAIA diagnose subset: fix-suggestion quality (manual rubric) improves; no localization regression |
| **G4** Explore mode | opt-in `mode='explore'`: run the text channel through `run_react_loop` with the §4 trace tools (same seam as GUI) | M | A/B vs macro-turns on Who&When 42-sample; ship only if ≥ |
| **G1** Unified facade | `DeepDebug.analyze(trajectory)` auto-routes: screenshot artifacts/`source_dir` → GUI channel; else text channel; one config surface | S | existing tests green; CLI `--mode` still forces |

Order: G2 → G5 (cheap, knowledge-side) → G1 (facade) → G3 → G4 (needs eval
budget). GAIA rerun does not block on G3/G4.

## 9. What DeepDebug is *not*

- Not a judge replacement: the one-call judge stays the cheap default; DeepDebug
  is the escalation tier (~6 calls) for when strict localization matters.
- Not a replayer: no re-execution of the debugged agent's tools.
- Not a committee of agents: one agent, multiple turns, complementary readings.
