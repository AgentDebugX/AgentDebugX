# Inspect Workflow

Inspect provides human-facing views over traces, Diagnose reports, and related
artifacts.

## When to use

Use Inspect when a developer needs to explore AgentDebugX artifacts manually
instead of reading raw JSON.

## Flow

1. Load stored traces or report artifacts.
2. Build a traceback-style or UI-oriented representation.
3. Serve data through the local inspection API when requested.
4. Keep the inspected artifact unchanged unless an explicit write operation is
   requested.

## Dependencies

Traceback-style inspection is local. The UI server requires the `ui` extra,
which installs FastAPI and Uvicorn.

## Extension Rules

- Keep display logic separate from schema models.
- Treat inspection as read-only by default.
- Put API and web serving code under `inspect/ui/`.
- Do not make Diagnose depend on Inspect.

