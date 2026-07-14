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

## UI boundaries

The FastAPI UI is a surface layer:

- `ui/app.py` assembles the application.
- `ui/routes.py` defines HTTP endpoints and server-side Rerun policy.
- `ui/views.py` owns HTML, CSS, and browser behavior.
- `ui/services.py` adapts Diagnose and Rerun workflows for the UI.
- `ui/branch_store.py` owns local case and debug-branch persistence.
- `ui/server.py` remains a compatibility import path.

Live UI reruns prefer `AGENTDEBUG_RUNNER_URL` and may use
`AGENTDEBUG_RERUN_COMMAND` as a process fallback. They default to `from_start`.
True `from_event` execution requires both runner capability and
`AGENTDEBUG_UI_RERUN_POLICY=from_event`. Runner credentials and commands stay on
the server and are never supplied by the browser.

## Dependencies

Traceback-style inspection is local. The UI server requires the `ui` extra,
which installs FastAPI and Uvicorn.

## Extension Rules

- Keep display logic separate from schema models.
- Treat inspection as read-only by default.
- Put API and web serving code under `inspect/ui/`.
- Do not make Diagnose depend on Inspect.
