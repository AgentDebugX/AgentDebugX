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

## Error state

`attribute.trajdebug` runs cluster -> state -> select:

1. **Cluster** findings by the concrete text they violate (`reference_quote`)
   rather than by taxonomy label, so repeated symptoms do not each count as a
   separate candidate. Findings with no reference quote each become their own
   instance, because wrongly merging two real errors hides one of them.

   Measured against TrajDebug's own LLM clustering on 100 ALFWorld
   trajectories, this collapses 29.6 triggers to 22.7 instances where theirs
   reaches 11.0 -- it catches exact repeats and little else, because the
   detector quotes differently-worded spans for the same violated object.
   Closing that gap needs an LLM-backed pass; keeping it deterministic is what
   makes the no-LLM path work at all.
2. **Classify** each instance: was it fixed, does it reach the terminal
   failure, and how. Needs an LLM; skipped when none is supplied.
3. **Select** the earliest instance still in the causal chain.

Clustering and selection are model-free, so with no LLM this degrades to
clustering plus earliest-in-chain -- still more than ranking by step index
alone, at no cost. A model that is unreachable or returns unparseable output
degrades the same way rather than failing the attribution.

`Blame` carries the result:

- `fix_status` + `fix_evidence_quote` -- e.g. `"fixed_at_step_39"`, with the
  text showing the agent correcting course. Without the quote, `fix_status` is
  an assertion rather than a checkable claim.
- `chain_membership` -- does this error actually reach the failure?
- `terminal_connection` -- how, e.g. `"budget_debt"` for an error that never
  broke correctness but consumed the run's budget
- `wasted_steps`

An instance with no state is treated as in-chain: absence of evidence must not
silently exclude a candidate. Only an explicit `chain_membership: false`
demotes one.

Adapted from TrajDebug phase C (THU-KEG/TrajDebug, MIT).

## Corrected actions vs. recovery

A `Blame` may carry a `CorrectedAction`: the one concrete action that should have replaced
the blamed step's action, in the trace's own `{"tool", "args"}` shape.

This is attribution, not recovery, and the boundary is worth stating precisely because it
is easy to blur:

- A **corrected action** is the counterfactual that makes the blame falsifiable. "Step 7
  was decisive" is an untestable claim on its own; "step 7 was decisive because it should
  have been `take(plate 1)`" can be tested by re-running the trajectory with exactly that
  one step substituted. It is graded against what the step actually did
  (`differs_from_original`), because a "correction" identical to the original proves
  nothing.
- A **recovery proposal** (`FixProposal`, Recover stage) is what to do *next*: a retry
  directive, a compensation, a guardrail, a rule. It is forward-looking and does not have
  to correspond to any single step in the failed trace.

Attributors still must not emit recovery plans, retry policy, or compensations.

It is opt-in per attributor (`propose_corrected_action=True`), always nullable, and never
guessed. `AttributionResult.raw['corrected_action']` reports why one is absent, so "not
asked", "no model to ask" and "asked and declined" stay distinguishable.

## Extension Rules

- Register new attributors with `diagnose/component_manifests/attribute/`.
- Keep component metadata in JSON and implementation in this package.
- Return structured, evidence-bearing outputs rather than free-form text only.
- Do not generate recovery actions here; leave that to Recover. Naming the counterfactual
  replacement for the blamed step (see above) is part of the attribution claim and is the
  one exception.
- An attributor that cannot produce something honestly returns null and says why, rather
  than returning a plausible guess a consumer cannot tell apart from a real answer.
