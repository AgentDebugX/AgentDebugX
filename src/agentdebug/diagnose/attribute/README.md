# Attribute

Attribute is the second Diagnose stage. It connects detected failure evidence to
probable root causes.

## When to use

Use Attribute when the system needs to answer: "Why did this run fail?"

Typical attribution methods include:

- heuristic mapping from failure type to cause
- step-by-step trace localization
- binary search over trajectory segments
- counterfactual analysis
- DeepDebug-style memory and reasoning
- mixture-of-experts localization

## Flow

1. Receive findings from Detect.
2. Inspect the relevant trajectory spans, tool calls, model outputs, and state.
3. Produce root-cause hypotheses with evidence.
4. Pass attributed findings to Recover.

## Dependencies

Simple heuristic attribution is local. DeepDebug, counterfactual, and LLM-based
attribution may require configured model clients or optional integration
dependencies.

## Extension Rules

- Register new attributors with `diagnose/component_manifests/attribute/`.
- Keep component metadata in JSON and implementation in this package.
- Return structured, evidence-bearing outputs rather than free-form text only.
- Do not generate recovery actions here; leave that to Recover.

