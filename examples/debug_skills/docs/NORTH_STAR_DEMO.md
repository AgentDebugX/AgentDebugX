# Agents That Debug Agents: Cross-Framework Trajectory Debugging with AgentDebugX

**Abstract.** Long-horizon LLM agent failures rarely manifest as exceptions:
tool calls report success, the run terminates normally, and the final answer
is wrong, with the causally responsible event buried tens of steps upstream.
We demonstrate AgentDebugX, an open-source post-mortem debugger for agent
trajectories. Failed runs from heterogeneous frameworks (Hermes, OpenClaw,
Claude Code) are normalized into a common trajectory representation,
analyzed by deterministic and LLM-backed detectors, and reported as an
*AgentTraceback* — a stack-trace-style listing ordered from root cause to
manifested failure. Because the debugger is exposed to host agents as a
skill, the demonstration covers three settings of increasing strength: an
agent diagnosing another agent's failed run (cross-framework), an agent
diagnosing its own run (self-diagnosis), and a closed loop in which the
proposed remediation is applied and the previously failing task verifiably
passes.

*Draft status: this document is the exemplar north-star artifact, written
the way the final version should read — a demonstration section (appendix
style) rather than a full paper. Artifacts marked `[placeholder]` (figures,
transcripts, tables) have not been captured yet; all quoted diagnostic
output is real, checked-in output from `examples/debug_skills/`.*

---

## 1. Overview

AgentDebugX transfers classical post-mortem debugging (`gdb ./a.out core`:
load the crashed process, read the backtrace) to agent executions. Two
properties define the workflow demonstrated below: it is *post-mortem* —
the debugger ingests native session exports the frameworks already produce,
with no prior instrumentation of the failed run — and it is *agent-invoked*
— the debugger is installed into host agents (Claude Code, Hermes,
OpenClaw) as a skill, so the unit of interaction is a plain-language
request, and the host agent drives the CLI and returns an evidence-grounded
diagnosis. Every subject trajectory in this section is an unmodified export
of a real agent run that organically failed; no trace is hand-crafted or
fault-injected.

The demonstration proceeds as: setup (§2); cross-framework diagnosis, where
Claude Code debugs a failed Hermes run (§3); self-diagnosis, where an agent
debugs its own trajectory (§4); closed-loop repair, where the diagnosis is
applied and the task re-verified (§5); and a coverage summary (§6). Each
case study ends with the exact command sequence that reproduces it.

## 2. Setup

**Installation.** All demonstrations run from a fresh clone; the
deterministic analysis tier requires no network access and no model
credentials.

```bash
pip install agentdebugx
git clone https://github.com/<org>/AgentDebugX && cd AgentDebugX
```

**Skill installation.** A generator emits the debugger as an installable
skill for each supported host agent:

```bash
agentdebug integrations skill --platform claude   --target ~/.claude/skills
agentdebug integrations skill --platform hermes   --target ~/.hermes/skills/
agentdebug integrations skill --platform openclaw --target ~/.openclaw/skills
```

The skill instructs the host agent to invoke the CLI rather than reason
over raw trace JSON, to run the deterministic tier first, to escalate only
when configured, and to report conclusions with step indices and event IDs.

**The command ladder.** Whichever host invokes it, the debugger is driven
through the same two-command core:

```bash
# 1. normalize a native export into the AgentTrajectory IR
#    (formats: hermes, openclaw, openai_agents_spans, crewai_events,
#     langgraph_callbacks, messages, …; --format auto detects)
agentdebug ingest <native-session-file> --format auto --out run.trajectory.json

# 2. diagnose; --traceback renders the report as an AgentTraceback
agentdebug diagnose run.trajectory.json \
  --mode heuristic --attributor none --recovery none --traceback --no-color

# 3. optional escalation when model credentials are configured:
#    LLM judge, attribution backends, iterative DeepDebug
agentdebug diagnose run.trajectory.json \
  --mode deepdebug --attributor none --recovery none \
  --traceback --no-color
```

The deterministic tier (step 2: heuristic analyzers over a 19-mode failure
taxonomy, plus cross-event rules for repeated calls, repeated states, and
premature success) runs offline in milliseconds. Normalization preserves
source lineage metadata (message IDs, session IDs, timestamps) so every
finding remains auditable against the original export.

## 3. Case study 1: Cross-framework diagnosis

**Setting.** The subject trajectory is a genuine
[Hermes](https://github.com/NousResearch) CLI session on a
[GAIA](https://arxiv.org/abs/2311.12983) research question (comparing
measured time spans across two fast-radio-burst papers). The run spans 50+
steps of web search, PDF retrieval, and code execution, and terminates with
a max-iteration failure before producing the requested final answer. The
unmodified session export is checked in at
`examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl`.

The diagnosing agent is Claude Code with the generated `agentdebug` skill
installed. The interaction is a single natural-language request:

**Figure 1. Claude Code invokes AgentDebugX on a Hermes trajectory.**

The screenshot sequence is linked as full-size artifacts rather than
embedded inline, because table-rendered thumbnails are too small to read:

| Panel | Artifact |
| --- | --- |
| Request and skill activation | [`figure1a-claude-code-skill-load.png`](../artifacts/north_star/case_study_1/figures/figure1a-claude-code-skill-load.png) |
| Heuristic diagnosis | [`figure1b-heuristic-diagnosis.png`](../artifacts/north_star/case_study_1/figures/figure1b-heuristic-diagnosis.png) |
| Deep-mode second opinion | [`figure1c-deep-mode-diagnosis.png`](../artifacts/north_star/case_study_1/figures/figure1c-deep-mode-diagnosis.png) |

**Transcript 1.** The prompt passed to Claude Code is the full user-facing
entry point; the original Hermes trajectory itself is intentionally not
embedded here because it is a long native JSONL export. It is kept as an
auditable artifact at the path shown in the prompt.

```text
user    Use agentdebug to debug this failed Hermes trajectory:
        examples/debug_skills/trajectories/hermes/gaia/
          i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl
        Tell me the likely root cause, cite the evidence, and explain
        what should change for the next run.
```

**Artifact bundle.**

| Artifact | Path | Purpose |
| --- | --- | --- |
| Readable Claude Code export | [`claude-code-export.md`](../artifacts/north_star/case_study_1/transcripts/claude-code-export.md) | Clean transcript for readers. |
| Expanded commands | [`commands.md`](../artifacts/north_star/case_study_1/commands.md) | Shell commands hidden by Claude Code's transcript export. |
| Deterministic traceback | [`hermes-pdftotext.traceback.txt`](../artifacts/north_star/case_study_1/reports/hermes-pdftotext.traceback.txt) | Full diagnostic output. |

**Diagnostic output.** The deterministic tier returns the following
AgentTraceback (Listing 1, abridged; the complete output is linked in the
artifact bundle above):

```text
AgentTraceback (root cause first, manifested failure last):
    trace_id=20260702_184523_08ed49  framework=hermes
    goal="I read a paper about multiwavelength observations of fast radio bursts…"

      File "root cause", in trajectory
        Step 14  agent=terminal  mode=system.tool_execution_error
          event_id=evt_453c7cd003e84700ab21b1f51653b5b2
          error>  {"output": "/usr/bin/bash: line 3: pdftotext: command not found",
                   "exit_code": 0, "error": null}
          suggested: Capture tool stderr/status/latency and classify retryable
                     versus non-retryable failures.
    ↓ cascaded to
      File "cascade depth 1", in trajectory
        Step 37  agent=terminal  mode=action.format_error
          event_id=evt_b9dee2448a2e43c1bf31f0523b2fe12c
          input>  python3 << 'PY'  # improvised PDF parsing with pdfplumber…
    ↓ cascaded to
      File "cascade depth 2", in trajectory
        Step 44  agent=web_search  mode=planning.inefficient_plan
          event_id=evt_9f4740ac702846bea0bfdde204431380
          evidence:
            - state repeated 3x within window of 4 events
          suggested: Add loop detection over tool calls and state deltas.
    ↓ cascaded to
      …

AgentFailure[planning.inefficient_plan]:
    Likely root cause: Tool execution error in terminal at step 14.
```

The host agent's returned summary, grounded in the listing:

> Root cause: the execution sandbox lacks `pdftotext` (step 14, event
> `evt_453c7cd003e84700ab21b1f51653b5b2`). The failure was invisible to the
> agent itself: the shell wrapper reported `exit_code: 0`, so the error was
> never registered. The agent then improvised a pdfplumber-based parser
> (step 37), repeated web search after failing to extract the PDF content
> (steps 44–50), and ultimately exhausted its tool-calling budget without a
> final answer. Recommended changes for the next run: install `poppler-utils`
> or another PDF-text extractor, expose unavailable tools as hard failures,
> and add loop/constraint checks before the agent burns the remaining budget.

**Observations.** The manifested failure (the agent ignores the final
stop-and-answer warning and reaches the tool-call budget at step 64) and the
root cause (a missing system dependency at step 14) are separated by 50
events, and the root cause is embedded in a tool result that reported
success — the configuration in which both manual inspection and
span-waterfall visualization are least effective. The deterministic
diagnosis runs offline with no model credentials.

**Reproduce.**

```bash
# direct CLI (offline, no credentials):
agentdebug ingest examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl \
  --format hermes --out out/hermes.trajectory.json
agentdebug diagnose out/hermes.trajectory.json \
  --mode heuristic --attributor none --recovery none --traceback --no-color
# expected: AgentTraceback with root cause system.tool_execution_error at
# step 14 and a cascade through steps 37–50, as in Listing 1

# agent-invoked (after skill installation, §2): open Claude Code and issue
# the request of Transcript 1 verbatim
```

## 4. Case study 2: Self-diagnosis

The debug contract is host-agnostic, which permits the recursive
configuration: an agent diagnosing its own trajectory.

### 4.1 Hermes on its own trajectory

Hermes, with the skill installed under `~/.hermes/skills`, is asked to
diagnose the same session export from Case study 1 — a trajectory it
produced itself:

```text
/agentdebug examples/debug_skills/trajectories/hermes/gaia/…5f982798.jsonl
```

> **[Figure 2 — placeholder]** *Screenshot of the Hermes session: the
> `/agentdebug` invocation, the identical ingest/diagnose ladder, and
> Hermes's summary of its own failure. This flow has been exercised; a
> presentable recording is pending.*

The intended observation: Figure 2 and Transcript 1 exhibit the *same
command ladder and the same report contract* under two different host
agents.

**Reproduce.**

```bash
agentdebug integrations skill --platform hermes --target ~/.hermes/skills/debugging
# in a Hermes session:
#   /agentdebug examples/debug_skills/trajectories/hermes/gaia/…5f982798.jsonl
# expected: same root cause and step index as Case study 1
```

### 4.2 Claude Code on its own session export

Claude Code sessions are themselves trajectories, persisted as JSONL under
`~/.claude/projects/<project>/<session-id>.jsonl`. A session that failed at
a task can therefore be handed to a fresh Claude Code session for
diagnosis. The presentable demo fixture for this section is still being
selected; it should be a real coding-task failure with a pre-declared test
or repro command as the oracle.

```text
user    My last session went off the rails. Use agentdebug to determine
        what happened: <approved-claude-code-session-export>.jsonl
```

```bash
agentdebug ingest <approved-claude-code-session-export>.jsonl \
  --format claude_code --out out/self.trajectory.json
agentdebug diagnose out/self.trajectory.json \
  --mode heuristic --attributor none --recovery none --traceback --no-color
```

> **[Figure 3 — placeholder]** *Screenshot of a Claude Code session
> diagnosing a prior Claude Code session's export: skill activation, ingest
> with `--format claude_code` or auto-detection, and the resulting
> traceback. The importer is implemented; the remaining work is choosing an
> approved failed coding session and recording the host-agent diagnosis.*

A debugger able to analyze the framework it runs inside is the standard
self-hosting milestone, and the configuration is practically motivated:
retrospective analysis of long, expensive host-agent sessions is a
recurring need for heavy users of these tools.

## 5. Case study 3: Closed-loop repair

A diagnosis is validated by the outcome of acting on it. This case study
closes the loop — diagnose, apply the remediation, rerun, verify — which
imposes two requirements on the substrate: the rerun must be replayable, and
task success must be independently checkable. We therefore use a real agent
run in a controlled environment: an OpenClaw failure against
[Claw-Eval](https://arxiv.org/abs/2604.06132)'s local mock services (mail,
calendar, helpdesk, CRM, finance), where every task carries human-verified
rubrics and the environment is deterministic. The failure itself is organic
— a real agent genuinely failed the task; only the environment is
simulated.

**Protocol.**

1. Diagnose the failed run with the ladder of §2.
2. Apply the recovery guidance to the next run (a prompt constraint, a
   guard, or an environment fix; remediation is propose-only until
   approved).
3. Rerun the identical task in the identical environment; score with the
   identical rubrics.

> **[Table 1 — placeholder]** *Before/after pair for one Claw-Eval task
> (e.g. email triage): traceback root-cause finding, rubric task score, and
> pass/fail, for the failed run and the post-remediation rerun. Target
> shape:*
>
> ```text
> Before  root cause: <mode> @ step k   task_score: 0.42  passed: false
> After   no root-cause finding         task_score: 0.97  passed: true
> ```

**Reproduce** (command sequence to be finalized with Table 1):

```bash
# 1. diagnose the failed run
agentdebug ingest <failed-claweval-run>.jsonl --format openclaw --out out/task.trajectory.json
agentdebug diagnose out/task.trajectory.json \
  --mode heuristic --attributor none --recovery none --traceback --no-color
# 2. apply the suggested remediation, rerun the identical task against the
#    local mock services, and re-score with the benchmark's rubrics
# 3. diagnose the rerun: expected no root-cause finding, task passes
```

The Case study 1 failure admits the same approach — its root cause is a
one-command environment fix (`apt-get install -y poppler-utils`) — and is
included as a secondary instance, with the caveat that its rerun traverses
the live web and is therefore nondeterministic.

**Disclosure.** We report the number of reruns performed. If a rerun fails
for a new reason, that result is reported as such ("original root cause
eliminated; next bottleneck: X") rather than discarded.

## 6. Coverage summary

| Diagnosing host (via skill) | Subject trajectory | Analysis tier | Status |
|---|---|---|---|
| Claude Code | Hermes GAIA run | deterministic, offline | root cause at step 14; cascade to incorrect answer (§3) |
| Hermes | its own GAIA run | deterministic, offline | same contract, different host (§4.1; recording pending) |
| Claude Code | its own session export | deterministic, offline | importer implemented; failed coding-session fixture and recording pending (§4.2) |
| Claude Code | OpenClaw session export | deterministic, offline | importer and real fixture checked in |
| Claude Code | OpenClaw run on Claw-Eval mock services | diagnose → remediate → rerun → re-score | closed loop, rubric-verified (§5; pending) |
| any of the above | any of the above | LLM escalation (`--mode deep`) | attribution + DeepDebug; requires credentials |

## 7. Reproducibility notes

All subject trajectories are unmodified exports of real agent runs, checked
into the repository under `examples/debug_skills/trajectories/`. Case
studies 1 and 2 (and the diagnosis half of 3) are reproducible offline from
a fresh clone with no model credentials, using the per-case-study command
sequences above. LLM-tier results (`--mode deep`) are labeled with the
model used. Full, unedited host-agent session transcripts for each case
study will be published alongside the figures as an appendix.
