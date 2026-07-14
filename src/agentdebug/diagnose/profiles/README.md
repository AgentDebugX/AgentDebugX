# Diagnose Profiles

Profiles orchestrate complete Diagnose workflows across detection, attribution,
and recovery guidance. They are selected when a workflow owns the interaction
between those stages and should not be composed as a single stage component.

## DeepDebug

`deepdebug.py` implements the read-only DeepDebug workflow:

1. Read the complete trajectory and produce a global candidate.
2. Probe the trajectory structure with cascade or bisection localization.
3. Adjudicate the candidates with local evidence.
4. Produce a fixed root cause, evidence, and one actionable fix.

Every run exposes these stages as `DeepDebugRound` entries named
`global_read`, `structure_probe`, `cross_examine`, and
`diagnose_and_suggest`. The same entries are written to the standard
`DiagnosticReport.audit` field, so CLI JSON retains each stage's summaries,
duration, candidates, narrowing decisions, and adjudication payload. The typed
`AaoMoeAnalysis` result preserves both
candidates, every cascade/bisection narrowing decision, the final candidate
window, and the adjudication verdict. `DeepDebugDiagnosis` carries the final
summary, evidence, and suggestion after localization is fixed.

Candidates carry `event_id`, `step_index`, and `agent_name`. Resolution checks
that identity tuple and only falls back to a bare step when the step is unique,
so repeated step numbers across agents cannot silently select the wrong event.

Final evidence uses `{event_id, quote}` references. Each quote is checked
against that event's original input, output, or error text. Rejected model
quotes are counted in the audit metadata; when none survive, DeepDebug uses a
deterministic excerpt from the resolved root event instead.

The profile may consume attribution algorithms and memory services, but those
remain independently owned by `diagnose/attribute/`. It never executes the
original agent or performs the Rerun stage.

The CLI runs deterministic Detect first and passes its findings into DeepDebug
as fallible prior signals. DeepDebug records its own primary attribution in the
standard report and preserves the upstream Detect summary for Recover.
`DiagnoseContext` then makes that primary attribution the recovery target, and
DeepDebug recovery converts the final fix into the standard retry-directive
shape consumed by Rerun.

## Compatibility

`agentdebug.diagnose.attribute.deepdebug`, `agentdebug.diagnose.deep`, and
`agentdebug.deep` remain supported import paths. They re-export the canonical
implementation from this package.
