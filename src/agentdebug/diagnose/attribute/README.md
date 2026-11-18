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
- dependency-guided value-provenance tracing

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

## Mixture of agents: N proposers, then a summarizing agent

`attribute.mixture_of_agents` (`moa.py`) runs N proposer attributors on the same
failed trajectory and then makes ONE more model call, the summarizer, which
reads their proposals and writes a single diagnosis in the ordinary `Blame`
schema.

It is not the same thing as either of the multi-reader paths already here:

- `EnsembleAttributor` merges backends with arithmetic in Python (Borda points
  or a Bayesian combination). No model sees another model's answer.
- `AaoMoeAttributor` (`moe.py`) is mixture-of-EXPERTS: two structurally
  different readings plus a tie-break call, with the second expert gated on the
  trace's structure.
- `MixtureOfAgentsAttributor` is mixture-of-AGENTS: N interchangeable
  proposers, usually one method over several models or one model at several
  seeds, plus a summarizer stage that is a real call. A vote can only return a
  step somebody named; a summarizer can reject the majority and can name a step
  no proposer named.

Guarantees worth knowing before using it:

- Proposers never see each other. Each runs against its own attributor and its
  own client, and the summarizer is the only stage that sees more than one
  reading.
- Proposals are sorted by `proposal_sort_key` (decisive step, then descending
  confidence, then `proposer_id`) before the summarizer prompt is built, so the
  prompt does not depend on declaration or completion order.
- Everything is kept: `AttributionResult.raw['mixture_of_agents']` carries each
  proposal with its proposer id, model, seed, status and rationale, and
  `hypotheses[1:]` are the proposals themselves behind the summary.
- `provenance.agrees_with_any_proposer` is False when the summary blames a step
  no proposer blamed. That is recorded, not repaired.
- Degradation is labelled. A proposer that errors or returns unparseable text
  becomes a dropout with a status; a summarizer that fails promotes the
  most-supported proposal and sets `summary_source` to
  `degraded_top_proposal`, never `summarizer`.
- Build proposers with `proposers_from_clients` or `proposers_from_seeds`. Both
  default to `fallback=NO_FALLBACK`, because the usual heuristic fallback would
  enter the panel as a vote from a model that never answered.

None of this is a correctness claim. Proposer agreement is not a label and
neither is the summary; nothing in the engine checks a diagnosis against the
environment, a gold annotation, or a rerun.

## Value provenance: blame the step that produced the value

`attribute.dependency_graph` (`dependency.py`, `--attributor dependency-graph`)
answers the question that usually precedes "which step was decisive": *which
step produced the value the failing step consumed?*

A malformed argument is rarely invented where it is used. A planner records
`refund_policy=waived` at step 1, a booking tool is called with that value at
step 3, and the tool rejects it at step 4. Earliest-step attribution reaches
step 1 only by accident; a model attributor gets there by reading the whole
trace. This one gets there deterministically, at zero token cost.

Producer and consumer are separate roles. A step **produces** the distinctive
tokens in its `output` (and in `error`, when a step reports a malformed value);
it **consumes** the tokens in its `input`. An edge `producer -> consumer` exists
only when a token one step produced is a token a later step consumed. A step
that merely mentions a value -- a consumer echoing an argument it was handed, a
string of ordinary English shared between steps -- therefore cannot become that
value's producer. User-origin events (`user_input`, `human.feedback`, or
`agent_name='user'`) are excluded from the producer role outright: they are
input to the run, not a link in its dataflow, so a value the user repeats is
never blamed upstream.

Attribution is then a graph walk, not a text scan. From the finding's step the
walk follows incoming producer edges on the quoted value and keeps going through
every earlier relay until it reaches the earliest producer in the chain, so
`step 1 -> step 2 -> step 3` resolves to step 1 rather than to the relay at step
2. Graph nodes are *events*, addressed by position in `trajectory.events`, since
several events can share a `step_index`; `span_id` therefore names the exact
event that produced the value, while `step_index` reporting stays an integer.

The finding's fields are read in strict priority order and are never pooled. A
non-empty `wrong_content_quote` is the only field traced: when no earlier step
produced the value it quotes, the finding's own step is blamed with
`no_upstream_producer`. It does not fall back, because `reference_quote` names
the *correct* constraint the step failed to satisfy, and blaming it for a wrong
value would convict the step that supplied the right answer. `reference_quote`,
and then `evidence`, are read only when the quote is empty.

- `sources=['dependency_graph', 'dependency_producer']`, with a verbatim excerpt
  around the matched value, so the edge can be checked against the trace instead
  of trusted.
- `raw['paths']` records the field that matched, the value, the producer step,
  the full `chain` of steps on the walk, and the alternate producers that were
  not chosen; `raw['graph']` records the directed edges (with both step indices)
  and the `terminal_step`, capped at `max_edges` with `edges_truncated` saying
  so. Traversal always uses the full graph; only the report is capped.
- No producer, no guess: the finding's own step is blamed and `sources` says
  `no_upstream_producer`.

Limits, stated because the output is a `Blame`:

- This is not a dataflow analysis. Two steps that pass the same identifier look
  connected whether or not one handed it to the other. A producer found this way
  is a candidate with provenance, not a proof of causation.
- It re-ranks and re-anchors findings and cannot derive them, so
  `requires_findings` is True for the same reason as `HeuristicAttributor`;
  called through `DiagnosePipeline` it always has findings.
- `chain_membership` follows the same contract as the other attributors: does
  the traced value reach the trajectory's *terminal* step, not merely one step
  later. Forward traversal only follows edges carrying the tracked value, so a
  step that passes some *other* value onward does not drag the chain to the end.
  `None` when there was nothing to track, `False` when the value dies mid-chain
  -- an isolated intermediate error the run did not ultimately fail on.
- With no model call there is nothing to ask what should have been done instead,
  so no corrected action is emitted (`reason='no_llm'`) -- the same report shape
  as `HeuristicAttributor`.

`candidate_steps(trajectory, findings)` runs the same graph walk and returns its
ranking as step indices: producers first (most shared values first, earliest
producer at each depth), then the findings' own steps. It is sized for
`BinarySearchAttributor(always_include_steps=...)`: a bisect can elide the
middle of a long prefix, and a producer the graph already located should not
have to depend on the window happening to include it.

Inspired by dependency-guided failure attribution (DeFA / FALAT); this
implementation is independent and model-free.

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
