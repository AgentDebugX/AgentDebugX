---
name: agentdebug
description: Debug failed or unclear LLM agent trajectories with AgentDebugX. Use for root-cause analysis, trajectory diagnosis, tool failures, repeated loops, wrong final answers, or cross-agent debugging.
---

# AgentDebugX Debug Skill

Drive the locally installed `agentdebug` CLI to debug failed or unclear LLM
agent trajectories. This same workflow applies whether the agent is debugging
its own run or a trajectory exported from another runtime.

Read references as needed:

- `references/setup.md` before first use, when `agentdebug` is missing, or
  when LLM-backed diagnosis fails due to missing credentials.
- `references/cli_reference.md` for exact `agentdebug` command forms,
  output files, exit codes, LLM configuration, and store usage.
- `references/formats.md` before converting a raw host export or when the
  input format is unclear.
- `references/analysis.md` after diagnosis when interpreting evidence,
  final-answer failures, or confidence.
- `references/recovery.md` when the user wants next-run guidance, repair
  suggestions, verifier ideas, or recovery planning.

## When To Use

Use AgentDebugX when the user asks to debug, diagnose, inspect, explain, or
find the root cause of an LLM agent trajectory or failed agent run.

Common triggers:

- failed agent run
- unclear or wrong final answer
- tool failure, timeout, missing file, permission denial, or bad tool args
- repeated loop or repeated failed tool call
- self-debugging or cross-agent debugging

## Inputs

Accept any of:

- AgentDebugX `AgentTrajectory` JSON
- Hermes session export JSON/JSONL
- OpenClaw session JSONL
- OpenHands event export or AgentDebugX-normalized trajectory
- AgentDebugX JSONL or SQLite trace store

If the user has not provided a trajectory/export path, ask for one. Do not
silently inspect host-local private state to find traces.

## Procedure

1. Run `agentdebug doctor` if availability is uncertain. If `agentdebug` is
   missing or LLM setup fails, read `references/setup.md`.
2. Create `.agentdebug/` if needed and write generated outputs there unless
   the user gave a different output directory.
3. If the input is a raw host export, normalize it:
   `agentdebug ingest <input> --format auto --out .agentdebug/<name>.trajectory.json`.
4. Prefer explicit formats when known:
   `--format hermes`, `--format openclaw`, or future `--format openhands_events`.
5. Verify the normalized trajectory exists and has events before diagnosing.
6. Run the deterministic pass first:
   `agentdebug diagnose <trajectory.json> --mode heuristic --attributor none --recovery none --traceback --no-color`.
7. If the user wants recovery guidance, rerun or run JSON output with an
   explicit recovery mode such as:
   `agentdebug diagnose <trajectory.json> --mode heuristic --attributor heuristic --recovery reflexion --out .agentdebug/<name>.report.json`.
8. If confidence is low and LLM credentials are configured, escalate with
   `agentdebug diagnose <trajectory.json> --mode judge --attributor all-at-once --recovery critic`.
9. If the failure is multi-step, ambiguous, or judge output is weak, use
   `agentdebug diagnose <trajectory.json> --mode deepdebug --attributor none --recovery none`.
   DeepDebug performs attribution and fix guidance internally; the two `none`
   values are required only by the current CLI compatibility contract.
10. Report the candidate root cause, step/event id, evidence, failure mode,
   confidence, and suggested fix.

## Ground Rules

- Do not manually inspect raw trajectory JSON when an AgentDebugX importer can
  parse it. Use the CLI.
- Do not make trajectory acquisition the default workflow; assume the user has
  provided an exported trajectory or ask for one.
- Do not apply fixes, rerun tools, or mutate a workspace unless the user
  explicitly approves.
- Recovery output is a proposal. Treat it as next-run guidance unless the user
  separately asks to implement or apply a fix.
- If confidence is missing or below 0.9, say "candidate root cause" or "likely"
  rather than claiming ground truth.
