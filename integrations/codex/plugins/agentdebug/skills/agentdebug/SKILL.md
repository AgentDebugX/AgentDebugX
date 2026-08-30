---
name: agentdebug
description: Use AgentDebugX only when the user explicitly asks for AgentDebug, AgentDebugX, or the agentdebug skill. Do not invoke for generic debugging or trajectory review.
---

# AgentDebugX Debug Skill

Use the locally installed `agentdebug` CLI to diagnose the current captured
host session or an explicitly supplied trajectory. Prefer DeepDebug: it is the
advocated AgentDebugX workflow and combines LLM-backed global analysis,
structure-guided localization, candidate adjudication, and fix guidance.

For the current session, run:

```bash
agentdebug run --current --profile deep --json
```

For a supplied trajectory or stored trace ID, run:

```bash
agentdebug run <input> --profile deep --json
```

Deep mode requires configured LLM credentials. Use `quick` only when the user
asks for a fast deterministic check or LLM access is unavailable; use
`standard` when the user explicitly prefers local attribution and guidance.

Read references as needed:

- `references/setup.md` before first use, when `agentdebug` is missing, or
  when LLM-backed diagnosis fails due to missing credentials.
- `references/cli_reference.md` for batch processing, lower-level commands,
  output files, exit codes, LLM configuration, and store usage.
- `references/formats.md` before converting a raw host export or when the
  input format is unclear.
- `references/analysis.md` after diagnosis when interpreting evidence,
  final-answer failures, or confidence.
- `references/recovery.md` when the user wants next-run guidance, repair
  suggestions, verifier ideas, or recovery planning.

## When To Use

Use this skill only when the user explicitly asks for AgentDebug, AgentDebugX,
or the `$agentdebug` skill. Handle generic debugging requests normally.

## Select The Target

- Use `--current` for this agent's current, latest, or just-completed captured
  session. Never substitute the newest trace from the store. If session-scoped
  capture context is unavailable, explain that capture is inactive.
- Use an explicit path or trace ID when supplied. Read `references/formats.md`
  if its format is unclear.
- If a past session is ambiguous, present candidates and ask the user to choose.
- Read `references/cli_reference.md` before processing a collection. A JSONL
  event stream may be one trajectory, so do not infer batch mode from its suffix.
- Use `--profile gui --format osworld` for one OSWorld trajectory. GUI RCA
  collections use the separate `python -m agentdebug.gui` workflow.

Invoke one primary `agentdebug run`. Preserve explicit format, profile,
diagnoser, attributor, or recovery choices. Add `--ui` only when the user asks
for interactive inspection.

## Read The Result

Read `status`, run/trace/report IDs, `resolved_pipeline`,
`candidate_root_cause`, `top_evidence`, `warnings`, and `errors`. Report them
compactly, then interpret the evidence for the user's objective:

```text
Status: <status>
Run: <run_id>
Trace: <trace_id>
Report: <report_id>
Candidate root cause: <summary or unavailable>
Evidence: <top evidence or unavailable>
Warnings/errors: <only when present>
```

Do not dump the full report JSON unless the user asks. Preserve the distinction
between trajectory facts, deterministic findings, LLM conclusions, recovery
proposals, and externally supplied labels.

For a current-session target, frame the diagnosis as self-reflection: identify
what this agent should change in its reasoning, verification, or next attempt.
Do not assume an AgentDebugX or host-framework code defect. For an external or
custom-agent target, connect findings to the relevant agent code, prompt,
skill, tool, or runtime only when the evidence supports that connection.

## Boundaries

- Let `agentdebug run` own ingest, diagnosis, and persistence. Do not invoke
  `ingest` or `diagnose` again after a successful run.
- Do not treat a diagnosis-only request as authorization to retry, patch, or
  mutate. Recovery output is a proposal unless the user asks to apply it.
- Always say "candidate root cause" or "likely" rather than claiming ground
  truth. Heuristic and DeepDebug reports intentionally omit confidence.
