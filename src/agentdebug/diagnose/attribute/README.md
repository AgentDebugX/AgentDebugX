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
- mixture-of-experts localization

## Flow

1. Receive findings from Detect.
2. Inspect the relevant trajectory spans, tool calls, model outputs, and state.
3. Produce structured root-cause hypotheses with grounded event identity and
   evidence.
4. Let `DiagnoseContext` promote the primary attribution for Recover while
   preserving the original Detect findings.

## Dependencies

Simple heuristic attribution is local. Counterfactual and LLM-based attribution
may require configured model clients or optional integration dependencies.

DeepDebug lives under `diagnose/profiles/` because it orchestrates multiple
Diagnose stages. It may reuse attribution algorithms and memory services, but it
is not registered as a regular Attribute strategy.

## Extension Rules

- Register new attributors with `diagnose/component_manifests/attribute/`.
- Keep component metadata in JSON and implementation in this package.
- Return structured, evidence-bearing outputs rather than free-form text only.
- Do not generate recovery actions here; leave that to Recover.
