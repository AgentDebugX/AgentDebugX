# AgentDebugX Skill Demo Story

This demo shows AgentDebugX as a product capability for agents: an agent can
use an installed `agentdebug` skill to normalize a failed run, diagnose the
failure, cite trajectory evidence, and propose a next-run recovery plan.

The demo is not meant to show a standalone observability dashboard. It is meant
to show an agent-invoked debugging loop over recorded trajectories.

## North Star

One AgentDebugX debug contract should let open-source agents debug each other:

```text
Claude Code can debug Hermes.
Hermes can debug itself.
Other host agents can call the same debugger workflow through their own skill
surfaces.
```

The product path is:

```text
native agent trajectory -> AgentDebugX AgentTrajectory -> diagnosis -> host agent response
```

Modern coding and research agents often fail after long tool-use chains. The
failure is not always a single exception; it can be a cascade: a missing system
dependency, a malformed tool result, repeated search loops, ignored constraints,
or an incorrect final answer after many apparently successful steps.

AgentDebugX gives the host agent a debugging skill for those failures:

1. Convert a native host trace into a common `AgentTrajectory`.
2. Run deterministic diagnosis to find visible failure signals and cascades.
3. Escalate to LLM-backed attribution for ambiguous or semantic failures.
4. Produce recovery guidance that is grounded in trace evidence.
5. Use the recovery guidance to improve the next run.

The important product point is that the user does not need to manually inspect
raw JSON. The agent uses the skill, calls the CLI, and reports the evidence.

This is not a dashboard-centered observability demo. The current-stage
principle is more direct:

```text
make agents use AgentDebugX to debug trajectories
```

## Primary Demo Scenario

The current strongest demo path is a real Hermes GAIA trajectory:

```text
examples/debug_skills/trajectories/hermes/gaia/
  i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl
```

This trajectory records a failed Hermes run on a research-style question. The
run includes normal agent reasoning, web/tool calls, a system dependency issue,
repeated recovery attempts, and a final-answer failure mode.

The intended live demo flow:

1. A user gives the trajectory path to a host agent, such as Claude Code, codex, Hermes agent.
2. The host agent loads the AgentDebugX skill.
3. The skill guides the agent to run `agentdebug ingest --format hermes`.
4. The agent runs deterministic diagnosis for the first evidence pass.
5. If credentials are configured, the agent runs LLM-backed attribution and
   DeepDebug for deeper root-cause localization.
6. The agent summarizes the likely root cause, cites event IDs or step indices,
   and provides recovery guidance.

The desired end-product experience is a closed loop: after diagnosis and
recovery guidance, the same or another agent should be able to retry the task
with the recommended constraints and finish correctly. The current example
assets demonstrate the diagnosis and recovery-proposal parts of that loop; the
automatic retry-and-verify loop is a next milestone.

## What The Demo Shows

### Native Trace Support

AgentDebugX ingests Hermes session exports directly. A Hermes trace keeps
session-level metadata such as source, model, token counts, timestamps, and
message counts, while each normalized event retains Hermes lineage metadata
such as message ID and session ID.

This example tree now keeps only real Hermes GAIA trajectories. Cross-runtime
support remains part of the product direction, but synthetic OpenClaw,
OpenHands, and native AgentDebugX fixtures were removed because they did not
help the current Hermes-first demo.

### Agent-Invoked Debugging

The skill is installed into a host agent, not run as a separate notebook or
manual script. The host agent is expected to:

- choose the correct importer,
- write outputs to a recoverable directory,
- run the deterministic baseline,
- escalate to LLM-backed methods when available,
- treat recovery output as next-run guidance, not as an applied fix.

### Evidence-Grounded Reports

The report should name the candidate root cause, the relevant event or step,
the failure mode, confidence, and the observed cascade. Good reports separate:

- upstream enabling failures, such as a missing tool dependency,
- manifested failures, such as ignoring a stop condition,
- recovery guidance, such as adding a verifier or hard guard for the next run.

### Recovery As A Product Feature

Recovery is not just a paragraph of advice. It is a structured part of the
diagnostic workflow:

- `reflexion` gives deterministic retry hints.
- `critic` gives verifier or guard suggestions.
- `self-refine` uses an LLM-backed critic/refiner loop for next-run guidance.

For an industrial or open-source demo, the strongest version should show that
the recovery proposal is actionable: rerun the task with the suggested guard or
environment fix, then compare the improved trajectory against the failed one.

## Suggested Demo Script

Use this short user-facing prompt in a host agent with the skill installed:

```text
Use agentdebug to debug this failed Hermes trajectory:

examples/debug_skills/trajectories/hermes/gaia/i-read-a-paper-about-multiwavelength-observation__5f982798.jsonl

Tell me the likely root cause, cite the evidence, and explain what should
change for the next run. 
```

Expected output from the host agent:

- A normalized trajectory under `.agentdebug/` or a user-specified output
  directory.
- A deterministic traceback or JSON report.
- An LLM-backed report when credentials are configured.
- A concise diagnosis with evidence and recovery guidance.

## Current Stage

Done:

- Hermes session export ingestion.
- Real Hermes GAIA trajectory fixtures.
- Recorded conversion and deterministic diagnosis script.
- Generated AgentDebugX skill with setup, format, CLI, analysis, recovery, and
  safety references.
- Skill-test packet flow for host-agent execution and Codex review.
- LLM-backed attribution and DeepDebug execution in a local skill test.
- Actual host-agent use of the skill has been tested with Claude Code and
  Hermes on real Hermes trajectories.

Not yet the main demo claim:

- The polished working demo/task flow is still ongoing.
- The generated skill still needs optimization based on raw, less-instructive
  host-agent runs.
- Automatic closed-loop retry after recovery.
- First-class batch splitting for multi-session Hermes JSONL exports.
- Full real-trace coverage for OpenClaw and OpenHands.
- UI/server workflow as the primary presentation surface.

The flagship proof for this stage is Hermes-first:

```text
Claude Code debugs a Hermes trajectory.
Hermes debugs its own trajectory.
Both use the same AgentDebugX command ladder and report contract.
```

The demo is successful when the host agent normalizes the Hermes export, runs
diagnosis, cites concrete evidence, and produces next-run recovery guidance.
The stronger north-star demo is still ahead: diagnose the failed trajectory,
apply an approved recovery, rerun the task, and verify the task finishes
correctly.

## Evaluation Criteria

A successful demo should make these points clear:

- The agent did not manually reverse-engineer raw trace JSON.
- The importer preserved enough host metadata to keep the diagnosis auditable.
- The deterministic pass produced a cheap baseline.
- LLM-backed methods were used only when configured and useful.
- Recovery guidance remained propose-only unless the user explicitly asked to
  apply it.
- The final answer was evidence-grounded, not just plausible.
