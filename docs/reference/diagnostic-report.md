# Diagnostic report

Every analyzer returns the same top-level `DiagnosticReport` model, even when the internal analysis method differs.

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `report_id` | string | Generated report identifier |
| `trace_id` | string | Source trajectory identifier |
| `task_id` | string or null | Optional task identifier copied from the trajectory |
| `generated_at` | timestamp | UTC report creation time |
| `root_cause_event_id` | string or null | Selected responsible event |
| `root_cause_agent` | string or null | Agent associated with the selected root cause |
| `root_cause_step_index` | integer or null | Step index associated with the selected root cause |
| `findings` | array | Localized failure findings |
| `summary` | string | Human-readable diagnosis summary |
| `suggestions` | array | Consolidated correction suggestions |
| `attribution` | object or null | Structured attributor output |
| `recovery` | object or null | Structured recovery proposals |
| `audit` | array | Auditable stage records when produced |
| `metadata` | object | Analyzer-specific provenance and supporting output |

## Failure findings

Each `FailureFinding` contains:

- a `FailureMode`,
- optional event, agent, and step localization,
- optional confidence,
- a list of evidence strings,
- an optional suggestion, and
- metadata describing how and why the finding was produced.

The deterministic analyzer records its rule pack, rule ID, trigger scope, and confidence basis under finding metadata.

## Attribution payload

When an attributor is enabled, the pipeline stores:

```json
{
  "method": "...",
  "elapsed_ms": 0,
  "hypotheses": [],
  "primary": null,
  "raw": null
}
```

`primary` is the first ranked hypothesis when any hypotheses exist.

## Recovery payload

When a recoverer is enabled:

```json
{
  "proposal_count": 1,
  "proposals": []
}
```

The top-level `suggestions` list is updated from the proposal text.

## GUI RCA metadata

The standard GUI RCA analyzer adds:

- `analyzer: "gui_rca"`,
- `source: "gui_rca"`,
- the model name,
- `per_step_summaries`, and
- `thinking_trace`.

Each inspected-step summary contains `step_num`, `intent_summary`, `outcome_summary`, and `summary_source`.

## Confidence behavior

Confidence is optional in the schema. Public serialization omits uncalibrated confidence from reports produced by the deterministic `HeuristicAnalyzer` and the `DeepDebugAnalyzer`. Other analyzers may retain their own confidence value and provenance.

## Use reports as hypotheses

A diagnostic report is evidence-bearing analysis, not automatic ground truth. Preserve the source trajectory and report provenance when reviewing, sharing, or using a report to prepare reruns.
