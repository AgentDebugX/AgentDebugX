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

## Dependencies

Core detection has no heavy dependencies. GUI and LLM detectors may require the
`gui` extra, an API key, or benchmark-specific data.

## Extension Rules

- Add deterministic rules as a new manifest-backed rule pack.
- Add broader detector components through `diagnose/component_manifests/detect/`.
- Keep rule metadata in JSON and executable logic in Python.
- Do not put attribution or recovery logic in this package.

