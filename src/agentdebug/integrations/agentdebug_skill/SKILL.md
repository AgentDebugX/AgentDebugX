---
name: agentdebug
description: Debug failed or unclear LLM agent trajectories with AgentDebugX. Use for root-cause analysis, trajectory diagnosis, tool failures, repeated loops, wrong final answers, or cross-agent debugging.
---

# AgentDebugX Debug Skill

Use the locally installed `agentdebug` CLI to create one durable debug run for
a trajectory supplied by the user or an external harness.

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

1. If no trajectory, export, store trace ID, or supported collection was
   supplied, ask the user for one. Never discover or snapshot the current host
   conversation.
2. Use `standard` unless the user asks for another profile. Use `gui` for a
   compatible CUA/GUI trajectory only when its LLM requirement has been
   disclosed. The `deep` and `gui` profiles may perform LLM-backed work.
3. Invoke exactly one primary operation:
   `agentdebug run <supplied-input> --profile <profile> --ui --json`.
   Pass through explicit `--format`, `--diagnoser`, `--attributor`, or
   `--recovery` choices instead of reinterpreting them.
4. Parse the returned object. Report its status, `run_id`, `trace_id`,
   `report_id`, resolved pipeline, candidate root cause, and top evidence.
   Preserve the distinction between trajectory facts, deterministic findings,
   LLM conclusions, recovery proposals, and externally supplied labels.
5. Offer the returned `ui_url` when present. If UI startup produced a warning,
   keep the successful diagnosis and report the warning separately.
6. Treat all recovery output as suggest-only. Do not apply a fix or execute a
   rerun without separate user authorization.

## Ground Rules

- Do not reproduce ingest/diagnose/recovery orchestration in shell commands;
  `agentdebug run` owns that state machine.
- Do not make trajectory acquisition the default workflow; assume the user has
  provided an exported trajectory or ask for one.
- Do not apply fixes, rerun tools, or mutate a workspace unless the user
  explicitly approves.
- Recovery output is a proposal. Treat it as next-run guidance unless the user
  separately asks to implement or apply a fix.
- Always say "candidate root cause" or "likely" rather than claiming ground
  truth. Heuristic and DeepDebug reports intentionally omit confidence.
