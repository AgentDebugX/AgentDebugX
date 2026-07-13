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

1. Receive attributed findings.
2. Select one or more recovery strategies.
3. Generate candidate recovery actions or rerun directives.
4. Preserve evidence and assumptions so the user can audit the recommendation.

## Dependencies

Template-based recovery can run locally. LLM-guided recovery requires configured
model access. Workflow-specific recovery modes may depend on benchmark or
executor context supplied later by Rerun.

## Extension Rules

- Register new recovery components with `diagnose/component_manifests/recover/`.
- Keep recovery outputs explicit about assumptions, scope, and expected effect.
- Do not execute external systems from Recover. Execution belongs to Rerun.
- Preserve compatibility with existing recovery mode names.

