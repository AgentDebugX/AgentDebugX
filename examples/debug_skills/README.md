# AgentDebugX Skill Integration Examples

This directory contains the runnable example assets for the AgentDebugX skill
integration. It is the public-facing companion to the generated skill code: the
files here should help another developer understand the layout, the integration
methodology, and the current stage of the demo.

For the polished demo narrative and current-stage status, read `docs/DEMO_STORY.md`.
For commands and prompts to run the demo, read `docs/USER_GUIDE.md`.

## Layout

```text
examples/debug_skills/
  README.md
  DEMO_STORY.md
  USER_GUIDE.md
  scripts/
    run_recorded_demo.sh
    prepare_skill_test_packet.sh
  trajectories/
    hermes/
      gaia/
  out/
```

`trajectories/` contains checked-in fixtures. `out/` is for generated local
outputs and should not be treated as source material.

## What Belongs Here

The examples are organized around recorded or exported trajectories. The
current primary path is Hermes:

- `trajectories/hermes/gaia/*.jsonl`: real Hermes GAIA task exports with
  final-answer failures.

Earlier synthetic OpenClaw, OpenHands, native AgentDebugX, and small Hermes
fixtures were intentionally removed from this example tree. They added
surface-area without helping the current demo: Hermes real-trace support is the
first path being made product-quality.

## Integration Methodology

The skill integration is product functionality, not a demo-only script. The
demo uses recorded traces because that is the current product stage:

1. A user or host agent provides an exported trajectory.
2. The host agent invokes the AgentDebugX skill.
3. The skill guides the agent to normalize the trace with `agentdebug ingest`.
4. The agent runs deterministic diagnosis as a cheap baseline.
5. The agent escalates to LLM-backed attribution and recovery when configured.
6. The agent reports evidence, root-cause candidates, and next-run recovery
   guidance.

This is intentionally different from a generic observability demo. The point is
agent-invoked root-cause analysis over trajectories, not a live tracing UI.

## Design Principles

- Start from user-provided or explicitly exported traces.
- Do not silently inspect private host state.
- Preserve host metadata during normalization.
- Keep output files recoverable and separate from source trajectories.
- Treat recovery as propose-only guidance unless the user asks to apply it.
- Prefer explicit host formats, such as `--format hermes`, when the host is
  known.
- Keep skill content split into setup, CLI reference, format, analysis,
  recovery, and safety references so host agents do not have to load one giant
  instruction file.

## Current Findings

The current Hermes examples show useful signal, but also reveal limitations:

- The diagnoses often identify surface failures first: missing binaries,
  syntax errors, malformed regexes, repeated tool calls, rate limits, and
  iteration limits. The deeper behavioral cause is often insufficient planning
  or failure to use the right available tool at the right time.
- The Hermes JSONL export preserves session metadata and messages, but it does
  not fully capture the live tool and skill affordances available to the agent
  at that moment. Pure post-hoc debugging on exported JSONL can miss that
  context.
- Deterministic traceback, LLM judge attribution, and DeepDebug can emphasize
  different failure points. Reports should separate upstream enabling failures
  from the manifested terminal failure.
- Recovery has been exercised as report output, but the stronger product demo
  is still a closed loop: diagnose, recover, retry, and verify that the task
  completes.
- The current demo path is file-based JSON/JSONL. SQLite stores are part of
  AgentDebugX, but the checked-in demo trajectories do not require store access.
