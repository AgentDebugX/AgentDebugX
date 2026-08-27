# Detect

Detect is the first Diagnose stage. It finds observable failure evidence in
agent events, trajectories, tool calls, model outputs, GUI states, and benchmark
results.

## When to use

Use Detect when the system needs to answer: "What looks wrong in this run?"

Typical signals include:

- explicit error events
- failed tool calls
- stuck or repeated actions
- malformed model output
- benchmark assertion failures
- GUI interaction failures

## Flow

1. Normalize events through the shared schema.
2. Run deterministic analyzers and rule packs.
3. Optionally run LLM or GUI-aware detectors.
4. Emit structured findings for Attribute.

## Rule Packs

Rule packs live under `detect/rules/packs/`. Each pack contains:

- `manifest.json` for metadata and entrypoint configuration
- `rules.py` for Python rule implementations
- `__init__.py` for package exports

The rule registry loads manifests first, then imports the configured entrypoint.
This keeps rule packs plugin-like while preserving Python implementation
quality.

## Evidence grounding

`detect.trajdebug` requires every finding to carry two verbatim spans and
verifies them before the finding leaves the analyzer:

- `wrong_content_quote` -- the wrong commitment, from the blamed event
- `reference_quote` -- what it contradicts
- `conflict_with` -- `task` / `context` / `self` / `env`, which scopes where the
  reference quote may come from and is what makes the requirement checkable
  rather than decorative
- `quote_verified` -- `True` / `False` / `None`

`None` means never checked and is the honest state for the deterministic rule
packs, which have no claim to quote. A consumer filtering for trustworthy
findings must not read `None` as `True`.

`agentdebug.diagnose.detect.evidence` holds the verification and is reusable by
any detector. `quote_verification_summary()` reports counts per report; the
ratio of verified to (verified + unsupported) is a measurable grounding rate
for a detector.

Whitespace is normalized before matching, and a trailing truncation marker is
stripped, because a model copying a span that runs to the end of what it was
shown copies the marker too. Nothing else is normalized, so an invented quote
still fails.

Adapted from TrajDebug Stage B (THU-KEG/TrajDebug, MIT).

## Context budget

Both LLM detectors render the trajectory by clipping each event to a fixed
budget -- 300 characters for `detect.llm_judge`, 3000 for `detect.trajdebug`.
That budget is spent uniformly and from the front of each field, so a long tool
result contributes its opening lines and loses the error at the end.

`detect.stage_a` (`compression.py`) offers the alternative. `StepCompressor`
summarises each step at three lengths (`th1` 1024 / `th2` 512 / `th3` 256
characters), and `GradedContextBuilder` renders the run with the region under
judgement at full detail and the rest as a gist, under one overall cap. When
the cap binds, the farthest steps lose their detail first and are dropped last.

Two short-circuits keep it affordable, and both are load-bearing rather than
optimizations: a step already inside the smallest tier is passed through with
no call, and a machine-generated step (diff, traceback, terminal output) is
clipped head-and-tail by `clip_middle` instead of summarised. On a coding trace
that is most of the volume. `StepCompressor.stats` reports how many calls each
path took, so a run can state its own compression cost.

Pass a builder as `context_builder=` to either detector. Omit it and the
detector renders exactly as before.

Adapted from TrajDebug Stage A (THU-KEG/TrajDebug, MIT).

## Root selection

A detector returns many findings; a report names one root cause. The policy
that reduces the list lives in `selection.py` rather than inline in each
analyzer:

- `earliest_finding` -- the earliest flagged step. The default, unchanged.
- `most_confident_finding` -- the finding the detector was most sure of,
  falling back to earliest when confidences are flat.

`earliest_finding` embeds a positional prior that holds while the detector
fires rarely and stops holding when it fires on nearly every step -- at which
point "earliest flagged" is just "earliest step". Naming the policy makes it
swappable via `root_selector=`, and lets an experiment attribute an accuracy
change to selection rather than to detection.

## Dependencies

Core detection has no heavy dependencies. GUI and LLM detectors may require the
`gui` extra, an API key, or benchmark-specific data.

## Extension Rules

- Add deterministic rules as a new manifest-backed rule pack.
- Add broader detector components through `diagnose/component_manifests/detect/`.
- Keep rule metadata in JSON and executable logic in Python.
- Do not put attribution or recovery logic in this package.

