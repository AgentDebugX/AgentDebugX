# North Star Demo Plan: "Agents That Debug Agents"

Status: proposal (2026-07-05). This document proposes the publishable demo
and the narrative artifact we ship with it.

Companion document: `NORTH_STAR_DEMO.md` in this directory is the exemplar
artifact itself — a paper demonstration/appendix-section draft (abstract
retained, motivation condensed to a short overview, then setup, case
studies with reproducible command sequences, and a coverage summary),
written the way we want the final piece to read, with real recorded output
where it exists and clearly marked `[placeholder]` figures/tables/
transcripts where it does not.

---

## 1. Thesis

> **AgentDebugX is the debugger agents call, not the dashboard humans watch.**

One sentence for the subtitle:

> Python tracebacks made runtime failures debuggable. AgentDebugX gives LLM
> agents the same primitive for failed trajectories — and lets any agent
> (Claude Code, Hermes, OpenClaw) invoke it as a skill, on any agent's
> trajectory, **including its own**.

The demo must prove three claims, in increasing order of strength:

1. **Cross-framework**: Claude Code debugs a real failed Hermes run.
2. **Recursive**: an agent debugs *its own* trajectory (Hermes self-debug;
   Claude Code ingesting its own session export).
3. **Closed loop**: the diagnosis is actionable — apply the recovery
   guidance, rerun, and the previously failing task passes.

## 2. How comparable tools present themselves (and why we do it differently)

Survey of the demo idioms readers already know (this section is about
*presentation style*, not architecture):

| Tool | Demo idiom | What the reader sees |
|---|---|---|
| [LangSmith](https://docs.langchain.com/langsmith/observability-quickstart) / [Langfuse](https://langfuse.com/docs/demo) / [Arize Phoenix](https://arize.com/docs/phoenix) / [AgentOps](https://docs.agentops.ai/v2/quickstart) | **Dashboard-first observability** | A trace waterfall screenshot; "add 2 lines, see your traces" (Langfuse even hosts a [live example project](https://langfuse.com/docs/demo) as the demo) |
| [MLflow 3 GenAI](https://mlflow.org/docs/latest/genai/tracing/) | **Autolog one-liner + UI, plus batch evaluation** | `mlflow.<framework>.autolog()` then a screenshot; [`mlflow.genai.evaluate`](https://mlflow.org/docs/latest/genai/eval-monitor/) scores traces with [LLM judges/scorers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/), incl. [Agent-as-a-Judge](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/agentic-overview/) that inspects the trace |
| [Sentry Seer / Autofix](https://sentry.io/product/seer/) | **Root-cause-to-fix narrative** | A stack trace becomes a root-cause analysis and a suggested patch ([docs](https://docs.sentry.io/product/ai-in-sentry/seer/autofix/), [GA announcement](https://blog.sentry.io/seer-sentrys-ai-debugger-is-generally-available/)) |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md) / [OpenHands](https://github.com/All-Hands-AI/OpenHands) | **Benchmark + trajectory viewer** | A leaderboard number plus a browsable trajectory (e.g. the [mini-SWE-agent inspector](https://mini-swe-agent.com/latest/usage/inspector/)) |
| [Claude Code Skills](https://www.anthropic.com/news/skills) launches | **Agent-invoked workflow transcript** | A terminal recording: user asks in plain English, the agent drives a CLI ([docs](https://code.claude.com/docs/en/skills), [engineering deep dive](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)) |
| [gdb](https://sourceware.org/gdb/current/onlinedocs/gdb.html/) / [pdb](https://docs.python.org/3/library/pdb.html) (the classic) | **Post-mortem debugging** | `gdb ./a.out core` — load the core dump, get the backtrace |

Positioning that falls out of this table:

- The observability tools all demo **capture**, and the more mature ones add
  **evaluation** — MLflow and LangSmith both score traces with LLM judges
  (MLflow's [Agent-as-a-Judge](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/agentic-overview/)
  even inspects the execution trace). But evaluation answers *"how good was
  this run?"* — a score with rationale, attached to the trace, viewed in
  their UI, over traces captured by their own SDK. Nobody demos
  **root-cause diagnosis**: *"which step broke, what cascaded from it, and
  what do you change."* Their centerpiece is "look at your trace" or "here
  is the grade"; ours is "here is the root cause, the evidence, and the
  fix." That is the Sentry idiom, not the LangSmith idiom — and Sentry's
  stack-trace-to-fix framing is the single most battle-tested
  developer-tools narrative in existence. We borrow it wholesale:
  **AgentTraceback is a stack trace for agent runs.**
- The closest analogy for the *invocation* style is the Claude Code Skills
  idiom: the demo artifact is a terminal transcript in which a user asks an
  agent in plain English and the agent drives the `agentdebug` CLI. No
  dashboard screenshot is required for the core demo (a differentiator worth
  stating explicitly: works offline, no server, no credentials for the
  deterministic pass).
- The gdb post-mortem analogy (`gdb ./a.out core` → `agentdebug diagnose
  trajectory.json --traceback`) is the fastest way to make a systems
  audience understand the product in one line. Use it early.
- Nobody in the table demos **an agent debugging another agent's run, or its
  own**. That is our unique frame and the title of the piece.

### 2.1 Register

The exemplar (`NORTH_STAR_DEMO.md`) is written as a **demonstration section
of a paper (appendix style), not a full paper**: the abstract keeps the
paper-narrative framing, but motivation is condensed to a short overview,
and the body is usage-focused — setup, case studies presented
transcript-first, and a per-case-study "Reproduce" command sequence.
Positioning material (relation to observability/evaluation tooling) and the
fleet-scale evaluation live in the paper body and in this plan, not in the
demo section. This keeps the same text liftable into an industrial paper's
appendix with minimal editing, and lightening the tone for a blog cut is
far easier than the reverse. Terminology: the demonstration units are
**case studies** (1–3) plus a **fleet-scale evaluation**; do not call them
"acts" or "scenes" in any published cut.

### 2.2 Showing skill invocation, not command dumps

A known failure mode of the draft demo material: it reads as a person
running CLI commands, when the claim being demonstrated is that *the host
agent* runs them via the skill. Presentation devices, in the order we should
apply them:

1. **State the contract once, then go transcript-first.** The CLI ladder
   (ingest → diagnose → escalate) appears exactly once, in a "debug
   contract" / methods section. Every case study thereafter leads with the
   *host-agent session*: the user's plain-language request, the skill
   activation, the agent's tool calls (shown as collapsed tool-call lines,
   the way the host UI renders them), and the agent's evidence-cited
   summary. Bare command blocks inside case studies are the exception, not
   the spine. *(Adopted in the exemplar.)*
2. **Annotated screenshots as figures.** One per case study: the real host
   UI (Claude Code TUI, Hermes CLI) with numbered callouts — request, skill
   trigger, CLI invocations, diagnosis. Screenshots prove skill invocation
   in a way stylized text cannot. *(Placeholders added in the exemplar.)*
3. **Full unedited transcripts as an appendix.** The in-body transcripts
   are abridged; auditability comes from publishing the complete session
   logs alongside. This is also the academic norm. *(Committed to in the
   exemplar's reproducibility section.)*
4. **Optional: short asciinema/GIF** of the Claude Code interaction for the
   repo page and blog cut only — not load-bearing for the paper cut.
5. **Considered and rejected: side-by-side two-column layout** (conversation
   left, commands right). Hard to typeset in Markdown and in two-column
   paper formats; the collapsed tool-call lines inside the transcript convey
   the same information.

## 2.5 Environment strategy: synthetic env ≠ synthetic failure

Which world should the demo trajectories come from — simple benchmark tasks,
real use cases in native environments, a synthetic benchmark environment
(Claw-Eval / AgentWorld-style mock services), or real MCP connections to live
email/message clients?

**The hard rule that settles it: the environment may be synthetic, but the
failure must be organic.** A debugger demo dies the moment readers suspect we
injected the bug we then "found". Every trajectory in the artifact must come
from a real agent genuinely failing at a task — never a hand-crafted or
fault-injected trace.

Scored against what a published demo needs:

| Substrate | Organic failure | Verifiable root cause | Reader-reproducible | Relatable task |
|---|---|---|---|---|
| Real Hermes GAIA run (native env, live web) | ✅ | ✅ GAIA gold answers | ✅ fixture checked in; diagnosis offline | medium (research-flavored) |
| Real use cases in native envs (live Slack, booking) | opportunistic — must wait for failures | ❌ no ground truth | ❌ privacy scrubbing, live services | ✅✅ |
| Claw-Eval mock services (`../claw-eval`: gmail, calendar, helpdesk, CRM, finance…) | ✅ `data/claw_eval` runs are real OpenClaw runs that organically failed | ✅ human-verified rubrics + audit snapshots | ✅✅ deterministic, local | ✅ tasks *are* the business use cases |
| Real MCP to live email/message clients | opportunistic | ❌ | ❌❌ credentials; real inbox data can never be a fixture | ✅✅ |

Decision, per demonstration unit:

- **Case studies 1–2**: keep the real Hermes GAIA trajectory as the
  flagship (real agent, real web, real 38-step cascade — the SWE-bench
  idiom), and add one Claw-Eval trajectory (email triage / helpdesk) as the
  second flagship for the industrial "my work agent failed" audience. Both
  already exist on disk; this is a selection task, not a construction task.
- **Case study 3 (closed loop)**: run it on Claw-Eval. Deterministic mock
  services plus rubric scoring make "fix → rerun → verify pass"
  reproducible, which neutralizes the rerun-nondeterminism risk in §8. The
  GAIA rerun (`poppler-utils` fix) stays as a stretch goal, not the case
  study's spine.
- **Fleet-scale evaluation**: Claw-Eval, as already planned.
- **Real MCP to live clients**: out of scope for the published artifact.
  Its only honest use is a short live-demo clip in a video; treat "capture
  from live MCP servers" as a roadmap item, not a demo substrate.

Using both substrates is itself part of the story: the GAIA trace shows the
debugger on the messy real thing; Claw-Eval shows it measurable in a
controlled world.

## 3. The demo: three case studies and a fleet-scale evaluation

### Case study 1 — Cross-framework diagnosis (flagship; fully recorded today)

Claude Code, with the generated `agentdebug` skill installed, debugs a real
failed Hermes GAIA run.

- **Fixture**: `examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl`
  — a genuine Hermes CLI session on a GAIA research question.
- **The story the trace tells** (this is why this fixture is the flagship —
  the failure is a *cascade*, not a single exception):
  1. Step 14: `pdftotext: command not found` — a missing system dependency,
     silently swallowed (`exit_code: 0`).
  2. Steps 37–50: the agent improvises PDF parsing, hits format errors,
     falls into repeated `web_search` loops (same state 3× in a 4-event
     window).
  3. Final step: a confident but wrong `FINAL ANSWER`.
- **User prompt** (verbatim in the recording):
  > Use agentdebug to debug this failed Hermes trajectory: `<path>`. Tell me
  > the likely root cause, cite the evidence, and explain what should change
  > for the next run.
- **What the agent does** (the skill guides it): `agentdebug ingest --format
  auto` → `agentdebug diagnose --mode heuristic --traceback` → optional
  `--mode deep` escalation when credentials exist → plain-English summary
  citing step indices and event IDs.
- **Centerpiece artifact**: the `AgentTraceback` output — "root cause first,
  manifested failure last", with `↓ cascaded to` arrows from the missing
  dependency down to the wrong answer. Already recorded at
  `examples/debug_skills/out/reports/hermes/gaia/…5f982798.traceback.txt`.

### Case study 2 — Self-diagnosis (the recursive hook)

Two configurations, same command ladder:

- **2.1 Hermes self-debug** (works today): Hermes with the skill installed
  under `~/.hermes/skills` runs `/agentdebug <path>` on its own failed
  session and explains its own failure. Proves the contract is not
  Claude-specific.
- **2.2 Claude Code debugs Claude Code** (the headline recursion; needs one
  build item): a Claude Code session that failed at a task is exported from
  `~/.claude/projects/<project-slug>/<session-id>.jsonl` and fed back into a
  fresh Claude Code session with the skill. The framework diagnoses its own
  trajectory.
  - **Gap**: no `claude_code` importer exists yet in
    `src/agentdebug/ingest/adapters/importers.py` (formats today: hermes,
    openclaw, openai_agents_spans, crewai_events, langgraph_callbacks,
    messages, …). This is the single most valuable build item in this plan —
    it turns "cross-framework debugging" into "self-hosting debugging", the
    same credibility milestone as a compiler compiling itself.

### Case study 3 — Closed-loop repair (diagnose → fix → rerun → pass)

The strongest possible proof of effectiveness, and the one thing every
observability demo lacks: show the fix *working*. Per §2.5, this case study
runs on a real agent run in a synthetic environment — an OpenClaw failure
against Claw-Eval's deterministic mock services — so the rerun is replayable
and the outcome is rubric-checkable.

- Pick an organically failed Claw-Eval run from `data/claw_eval/` with a
  relatable task (email triage / helpdesk) and a clear diagnosis.
- Diagnose it with the same command ladder as Case study 1; apply the
  recovery guidance to the next run (prompt constraint, guard, or
  environment fix — propose-only until approved).
- Rerun the same task in the same mock environment; score with the same
  rubrics: **no root-cause finding, task passes**.
- Present as a before/after pair: traceback + task score, failed vs clean.
- Stretch goal: the Case study 1 GAIA failure gets the same treatment
  (`pdftotext` missing → install `poppler-utils`), but its live-web rerun
  is nondeterministic, so it supports the case study rather than carrying
  it.
- Honesty rule: if the rerun still fails for an unrelated reason, we show
  that too — "the original root cause is gone; the next bottleneck is X" is
  itself a compelling debugging story. Do not cherry-pick silently; say how
  many reruns were made.

### Fleet-scale evaluation (industrial-paper tier only)

For the paper/industrial audience, single anecdotes are not evidence. We
already have the raw material:

- `data/claw_eval/` holds a batch pipeline over 50 real OpenClaw benchmark
  runs (`processed/sample_general/trajectories.jsonl`) with per-run
  AgentDebugX reports and a human review layer
  (`reviews/…/reviewed_annotations.jsonl` with `failure_mode_ids`,
  `root_event_id`, reviewer status).
- Report: blame-localization agreement between AgentDebugX (heuristic and
  LLM-judge modes) and human review labels; failure-mode distribution across
  the fleet; cost of deterministic pass vs LLM pass.
- This unit is a table and one figure, not a transcript. It backs the
  case-study narrative with numbers and is the section reviewers will
  actually check.

## 4. Asset inventory

| Asset | Status | Where |
|---|---|---|
| Generated skill (Claude / Hermes / OpenClaw) | ✅ exists | `agentdebug integrations skill --platform …` |
| Real Hermes GAIA failure fixtures (5) | ✅ exists | `examples/debug_skills/trajectories/hermes/gaia/` |
| Recorded ingest + heuristic diagnose outputs | ✅ exists | `examples/debug_skills/out/` |
| Flagship AgentTraceback (pdftotext cascade) | ✅ exists | `out/reports/hermes/gaia/…5f982798.traceback.txt` |
| OpenClaw importer (session + trajectory JSONL) | ✅ exists (commit 78094b2) | `src/agentdebug/ingest/adapters/importers.py` |
| Real OpenClaw GAIA fixture | ✅ exists | `examples/debug_skills/trajectories/openclaw/gaia/` |
| Hermes self-debug run | ✅ tested informally | needs a clean recording |
| **Claude Code session importer** | ❌ build | new `claude_code` format in importers.py |
| **Case study 2.2 recording** (Claude Code debugs itself) | ❌ record | depends on importer |
| **Case study 3 rerun pair** (before/after trajectories + rubric scores) | ❌ record | Claw-Eval task rerun in `../claw-eval` mock services; GAIA/`poppler-utils` rerun as stretch |
| **Fleet-scale numbers** (agreement vs review labels) | ❌ compute | script over `data/claw_eval/` |
| Annotated host-UI screenshots (Figures 1–3) | ❌ capture | one per case study, per §2.2 |
| Terminal recordings (asciinema or transcript) | ❌ record | one per case study |

Deterministic core (Case studies 1–2 and the "before" half of 3) runs with
no network and no LLM credentials — keep that property; it is a stated
differentiator and makes the demo reproducible by any reader.

## 5. Publication formats

One set of assets, three cuts:

1. **Repo README/`docs/` page** (ship first): essentially
   `NORTH_STAR_DEMO.md` with all placeholders filled in. Lives at
   `examples/debug_skills/` or `docs/`; the README links to it under a
   "See it debug a real run" heading. Success metric: a reader can reproduce
   Case study 1 in under 5 minutes from a fresh clone.
2. **Blog post** ("Agents that debug agents"): Case studies 1–3 with the
   recursion of 2.2 as the hook and the closed loop as the payoff. Lightened
   tone relative to the exemplar; terminal transcripts, one optional short
   asciinema/GIF of the Claude Code interaction. Comparison paragraph
   ("observability tools show you the trace; a debugger tells you why")
   drawing on §2.
3. **Industrial paper**: the exemplar drops in as the demonstration /
   appendix section (case studies 1–3 with reproduce blocks); the
   fleet-scale evaluation carries the quantitative table in the paper
   body, and the §2 positioning material feeds related work. GAIA and the
   Claw-Eval benchmark give the tasks external validity.

## 6. Execution order

1. Write the exemplar doc (done — `NORTH_STAR_DEMO.md`) and agree on the
   narrative before building anything.
2. Re-record Case study 1 as a clean transcript plus annotated screenshot
   (skill-invoked, not bare CLI) — half a day; everything exists.
3. Record Case study 2.1 (Hermes self-debug) the same way.
4. Build the `claude_code` importer (+ tests mirroring the hermes/openclaw
   importer tests), then record Case study 2.2.
5. Record Case study 3: pick a failed Claw-Eval run from `data/claw_eval/`,
   apply the recovery guidance, rerun the task against the local mock
   services, re-score with the rubrics, build the before/after table.
   Stretch: the GAIA rerun with `poppler-utils` installed.
6. (Paper only) Script the fleet-scale agreement computation over
   `data/claw_eval/`.

## 7. Acceptance criteria

- [ ] Case study 1 reproducible offline from a fresh clone, no credentials.
- [ ] Every diagnosis in the artifact cites a concrete step index and event
      ID that appears in the checked-in fixture.
- [ ] The host agent is always shown *using the skill*, never hand-parsing
      trace JSON — transcript-first per §2.2, with at least one annotated
      host-UI screenshot per case study.
- [ ] Case study 2 shows the identical command ladder in two different
      hosts.
- [ ] Case study 3 shows a verifiable pass (Claw-Eval rubric score flips to
      pass) and discloses rerun count.
- [ ] Every demo failure is organic — a real agent run that genuinely
      failed; environments may be synthetic, failures never are. No live
      MCP clients or real user data in any checked-in artifact.
- [ ] No claim in the artifact exceeds what a checked-in file or recorded
      transcript shows.
- [ ] The piece never requires a screenshot of a dashboard to make its
      point.

## 8. Risks

- **Case study 3 rerun nondeterminism**: largely neutralized by running the
  closed loop on Claw-Eval's deterministic mock services (§2.5). The
  residual risk sits in the stretch-goal GAIA rerun (live web search);
  mitigation: the honesty rule in Case study 3, plus that fixture's root
  cause being environmental (dependency install) rather than semantic.
- **Claude Code session format drift**: `~/.claude/projects/*.jsonl` is not
  a stable public contract. Mitigation: importer tolerates unknown fields,
  keeps raw lineage metadata like the hermes/openclaw importers already do,
  and we check in a fixture so the demo never depends on the live format.
- **LLM-judge variance in recordings**: keep the deterministic pass as the
  spine of every transcript; present `--mode deep` output as an escalation,
  clearly labeled with the model used.
