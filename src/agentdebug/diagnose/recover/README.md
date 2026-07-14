# Recover

Recover is the third Diagnose stage. It turns attributed root causes into
repair strategies that can be reviewed by a user or consumed by Rerun.

## When to use

Use Recover when the system needs to answer: "What should we try next?"

Typical strategies include:

- Reflexion-style retry guidance
- CRITIC-style feedback
- Self-Refine instructions
- DeepDebug recovery prompts
- AutoManual recovery plans
- Saga rollback suggestions

## Flow

1. Receive a `DiagnoseContext` containing detector findings and primary
   attribution.
2. Use the primary attribution as the recovery target. Fall back to detector
   findings only when attribution is unavailable or cannot be grounded to a
   trajectory event.
3. Select one or more recovery strategies.
4. Generate candidate recovery actions or rerun directives.
5. Preserve evidence and assumptions so the user can audit the recommendation.

Self-Refine requests JSON-constrained critic and refined-action fields. Each
stage validates `finish_reason`, JSON parsing, the required field, and sentence
completeness. A truncated or invalid response is retried once with an expanded
token budget; repeated failure produces deterministic guidance from the
finding instead of exposing partial model text. Gateways that reject
`response_format` automatically fall back to prompt-constrained JSON.

## Dependencies

Template-based recovery can run locally. LLM-guided recovery requires configured
model access. Workflow-specific recovery modes may depend on benchmark or
executor context supplied later by Rerun.

## Extension Rules

- Register new recovery components with `diagnose/component_manifests/recover/`.
- Keep recovery outputs explicit about assumptions, scope, and expected effect.
- Do not execute external systems from Recover. Execution belongs to Rerun.
- Preserve compatibility with existing recovery mode names.
