# Agent Instructions

This repository uses GSD planning artifacts under `.planning/`.

## Current Project

- Project context: `.planning/PROJECT.md`
- Requirements: `.planning/REQUIREMENTS.md`
- Roadmap: `.planning/ROADMAP.md`
- State: `.planning/STATE.md`
- Codebase map: `.planning/codebase/`

## Working Rules

- Keep CUA / GUI RCA migration work source-faithful: re-check current `GUIAgentDebug` files before carrying over metrics, RCA behavior, or rollout claims.
- Preserve the boundary between submitted-paper facts, experiment snapshot metrics, machine RCA outputs, and rollout provenance.
- Keep migration changes surgical and focused on AgentDebugX CUA / GUI RCA, rollout, tests, and directly related docs.
- Do not download model weights, datasets, checkpoints, or model caches unless explicitly approved.
- Prefer focused tests for RCA output shape, trajectory inspection, rerun evidence, and packaging/import behavior.
