# Recovery Guide

Recovery is an expected AgentDebugX feature. In the CLI it is selected through
`agentdebug diagnose --recovery ...`, not a separate command. Recovery output is
suggest-only by default: it proposes next-run guidance, verifier ideas, manual
rules, or rollback scaffolds. Do not apply fixes or rerun the original agent
unless the user asks.

## Recovery Modes

| Mode | What it produces | LLM required |
|---|---|---|
| `none` | No recovery payload. | No |
| `reflexion` | Per-finding retry reflection for the next attempt. | No |
| `critic` | Tool-grounded verifier or guard suggestions. | No |
| `self-refine` | Critic/refiner concrete next-action proposals. | Yes |
| `auto-manual` | Learned one-line rule/manual suggestion. | Optional |
| `saga-rollback` | Rollback scaffold for side-effecting actions. | No; CLI usually returns empty without compensations |

## Standard Recovery Runs

Fast local recovery:

```bash
agentdebug diagnose .agentdebug/<case>.trajectory.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery reflexion \
  --out .agentdebug/<case>.recovery.report.json
```

Verifier-oriented recovery:

```bash
agentdebug diagnose .agentdebug/<case>.trajectory.json \
  --mode heuristic \
  --attributor heuristic \
  --recovery critic \
  --out .agentdebug/<case>.critic.report.json
```

LLM-backed recovery for ambiguous or multi-step failures:

```bash
agentdebug diagnose .agentdebug/<case>.trajectory.json \
  --mode deepdebug \
  --out .agentdebug/<case>.deep.recovery.report.json
```

DeepDebug generates its own attribution and fix guidance, then automatically
packages the fix as a standard retry directive. Use `--recovery deepdebug` to
make that choice explicit, or `--recovery none` to omit the recovery payload.
Use `--recovery self-refine` with a regular mode when an independently attached
Self-Refine proposal is specifically desired.

Use `--traceback --no-color` when the user wants a readable cascade. Use JSON
`--out` when the user wants recovery proposals because the recovery payload is
structured in the JSON report.

## Interpreting Recovery Output

In JSON reports:

- `suggestions` contains user-readable proposal text.
- `recovery.method` is the selected recovery mode.
- `recovery.proposal_count` tells whether any proposals were generated.
- `recovery.proposals[]` contains proposal ids, target event ids, rationale,
  side effects, and approval requirements. LLM Judge workflows may also expose
  model-reported confidence; Heuristic and DeepDebug outputs omit it.

If `proposal_count` is zero, say that no recovery proposal was generated for
the selected strategy. Do not invent one. You may suggest trying another
strategy such as `critic` for verifier design or `self-refine` when an LLM is
available.

## Strategy Selection

- Use `reflexion` when the user wants a concise next-attempt instruction.
- Use `critic` when the finding implies a missing verifier, schema guard,
  final-state check, tool-result type check, handoff contract, or loop guard.
- Use `self-refine` when the failure is ambiguous and the user has LLM
  credentials configured.
- Self-Refine validates structured critic/action output and retries truncated
  generations with a larger token budget before using deterministic guidance.
- Use `auto-manual` when the user wants a persistent learned rule or manual
  entry. Treat filesystem writes as opt-in.
- Use `saga-rollback` only for side-effecting trajectories and only as a
  scaffold unless compensations are registered.

## Safety

- Recovery proposals are not permission to mutate a workspace.
- Do not auto-apply learned rules, patch prompts, retry tool calls, or roll
  back side effects without explicit user approval.
- If a proposed recovery has side effects or requires human approval, surface
  that fact directly.
- For final-answer failures, distinguish "what the next run should verify"
  from "the correct answer"; AgentDebugX may diagnose the trajectory without
  independently solving the task.
