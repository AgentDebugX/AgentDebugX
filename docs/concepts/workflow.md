# How AgentDebugX works

AgentDebugX separates failure analysis from retry execution.

```text
Your agent or exported trace
            │
            ▼
   AgentTrajectory schema
            │
            ▼
  Detect → Attribute → Recover
            │
            ▼
      DiagnosticReport
            │
            ▼
  plan / simulation / live rerun
```

## Ingest: create a portable trajectory

Agent frameworks record different shapes: messages, callbacks, spans, tool events, screenshots, or benchmark JSONL. Ingest adapters convert these shapes into `AgentTrajectory` and `AgentEvent` objects.

Downstream diagnosis code therefore works with one contract instead of framework-specific objects.

## Detect: identify visible failure signals

Detect produces `FailureFinding` objects from individual events or trajectory-level patterns. The deterministic analyzer loads manifest-backed rule packs; the CLI can also select LLM Judge, DeepDebug, or GUI RCA modes.

A finding records the failure mode, source event or step, evidence, optional confidence, and a suggested response.

## Attribute: locate responsibility

The final error is not always the root cause. Attribute tests which earlier event or agent likely introduced the failure.

The CLI currently exposes heuristic, all-at-once, step-by-step, binary-search, and counterfactual attributors. DeepDebug owns its own multi-round attribution workflow.

## Recover: package a proposed correction

Recover converts the diagnostic context into a structured fix proposal or retry directive. Available CLI strategies include DeepDebug, Reflexion, CRITIC, Self-Refine, AutoManual, and saga rollback.

Recovery remains suggest-only. It does not execute tools or mutate the target application.

## Rerun: test the hypothesis

Rerun consumes a diagnostic report and, when available, the source trajectory. It can:

1. create an auditable plan,
2. export pending actor tasks,
3. generate an explicitly labeled simulation, or
4. dispatch to an application-owned live executor.

Only observed live execution can provide evidence about the real task outcome. A simulation executes no tools and is not proof that a fix worked.

## Inspect and share

The optional local UI reads SQLite or JSONL trace stores. Error Hub bundles provide a scrubbed, portable unit for regression tests or opt-in sharing.

For detailed implementation boundaries, continue to [Architecture](../ARCHITECTURE.md).
