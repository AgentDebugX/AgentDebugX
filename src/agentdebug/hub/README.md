# Error Hub Workflow

Error Hub packages, scrubs, stores, and publishes reusable failure cases.

## When to use

Use Error Hub when a diagnosed failure should become a shareable artifact for
benchmarking, regression tests, documentation, or collaborative debugging.

## Flow

1. Collect traces, Diagnose reports, metadata, and optional recovery artifacts.
2. Scrub sensitive values before packaging.
3. Build a bundle with stable metadata.
4. Store the bundle locally or publish it through a configured backend.

## Dependencies

Local bundle creation is dependency-light. Remote publication depends on the
selected backend. Hugging Face publication requires the `hub-hf` extra and
credentials.

## Extension Rules

- Add storage backends through the backend interface.
- Keep scrubbing explicit and testable.
- Do not publish raw private traces by default.
- Keep legacy `diagnose/actions/hub` imports as compatibility shims.

